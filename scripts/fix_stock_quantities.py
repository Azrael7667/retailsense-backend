"""
Fix for oversized inventory valuation - updates ONLY stock_quantity on
existing products, using tiers that scale inversely with cost_price (cheap
fast-movers held in bulk, expensive slow-movers held in small counts).

Does NOT touch products' names/prices/categories, suppliers, customers,
invoices, purchases, payments, or expenses - only stock_quantity changes.

Run: python scripts/fix_stock_quantities.py
"""

import random
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

STORE_NAME = "Bijeta Auto Parts"
random.seed(11)  # different seed from the qty used originally - fresh distribution


def tiered_stock_qty(cost_price):
    """On-hand stock held, not a single order - so ranges run higher than the
    per-purchase-order tiers, but still scale inversely with unit cost."""
    if cost_price < 100:   return random.randint(100, 450)
    if cost_price < 500:   return random.randint(35, 150)
    if cost_price < 1500:  return random.randint(15, 65)
    if cost_price < 5000:  return random.randint(5, 25)
    return random.randint(2, 10)


def get_store_id():
    r = supabase.table("stores").select("id").eq("name", STORE_NAME).single().execute()
    if not r.data:
        raise ValueError(f"Store '{STORE_NAME}' not found.")
    return r.data["id"]


def fetch_all_products(store_id):
    """Paginates past Supabase's default 1000-row cap so all 1,136+ products
    actually get fixed, not just the first page."""
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        res = supabase.table("products").select("id,cost_price") \
            .eq("store_id", store_id).range(offset, offset + page_size - 1).execute()
        rows = res.data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows


def main():
    print("=" * 60)
    print("Fixing inflated stock quantities - Bijeta Auto Parts")
    print("=" * 60)

    store_id = get_store_id()
    print(f"Store ID: {store_id}")

    products = fetch_all_products(store_id)
    print(f"\nFetched {len(products)} products (paginated past the 1000-row cap)")

    updated = 0
    for p in products:
        new_qty = tiered_stock_qty(p["cost_price"] or 0)
        supabase.table("products").update({"stock_quantity": new_qty}).eq("id", p["id"]).execute()
        updated += 1
        if updated % 200 == 0:
            print(f"  ...{updated} updated")

    print(f"\nUpdated stock_quantity on {updated} products")

    # Verify new total stock value
    fresh = supabase.table("products").select("cost_price,stock_quantity") \
        .eq("store_id", store_id).range(0, 4999).execute().data
    total_value = sum((r["cost_price"] or 0) * (r["stock_quantity"] or 0) for r in fresh)
    print("\n" + "=" * 60)
    print(f"New total stock value: Rs {total_value:,.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
