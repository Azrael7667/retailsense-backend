"""
Fix for oversized purchase bills - regenerates ONLY purchases + purchase_items,
using tiered restock quantities (cheap fast-movers in bulk, expensive slow-movers
in small counts) instead of the flat 5-40 qty that inflated bills ~4-5x.

Does NOT touch products, suppliers, customers, invoices, payments, or expenses -
those were already correct and are left alone.

Run: python scripts/fix_purchases.py
"""

import random
from datetime import date, timedelta
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

STORE_NAME = "Bijeta Auto Parts"
START_DATE = date(2025, 9, 2)
END_DATE   = date(2026, 9, 1)
random.seed(7)

SUPPLIERS_WEIGHTS = [
    ("Sipradi Auto Parts Pvt. Ltd.", 40), ("Rita Automobiles Pvt. Ltd.", 14),
    ("Gautam Buddha Auto Spares Pvt. Ltd.", 8), ("BNH Auto Tech Pvt Ltd", 6),
    ("Bajrasatya Traders", 5), ("Satyadip International Pvt. Ltd.", 4),
    ("Zoya Trade Concern Pvt. Ltd.", 3), ("Ladali International Pvt. Ltd.", 3),
    ("Royal Auto Parts", 2.5), ("Darsan Trade Link Pvt. Ltd.", 2.5),
    ("Mahavir Auto Spares Pvt. Ltd.", 3), ("SDI Auto Mobiles Pvt. Ltd.", 1.5),
    ("S.E. International Pvt. Ltd.", 1.5), ("Auto Zone Traders", 1),
    ("Best Buy Auto Pvt. Ltd.", 1), ("Manakamana International", 1),
    ("Good Automobile Pvt. Ltd.", 1), ("Auto Spares Nepal Pvt. Ltd.", 1),
    ("Nepal Auto Parts Group Pvt. Ltd.", 1), ("Mahakali Automobiles Pvt. Ltd.", 0.8),
    ("Unique Enterprises & Traders", 0.8), ("Kapish Trade Concern", 0.6),
    ("New Kasaju Enterprises", 0.6), ("Priyadip Traders", 0.6),
    ("Next Generation Automotive Pvt. Ltd.", 0.6), ("Himalayan Auto Distributors", 0.5),
    ("Everest Motor Spares Pvt. Ltd.", 0.5), ("Annapurna Auto Traders", 0.5),
    ("Machhapuchhre Auto Parts", 0.5), ("Bagmati Trade Concern", 0.4),
    ("Sagarmatha Auto Spares", 0.4), ("Trishuli Motor Traders", 0.4),
    ("Gorkha Auto Distributors", 0.4), ("Lumbini Auto Parts Concern", 0.3),
    ("Koshi Auto Spares Pvt. Ltd.", 0.3), ("Karnali Trade Link", 0.3),
]


def tiered_qty(cost_price):
    """Cheap fast-movers get restocked in bulk; expensive slow-movers in small
    counts - matches how an auto parts shop actually buys (you don't order
    40 alternators, but you might order 40 oil filters)."""
    if cost_price < 100:   return random.randint(15, 70)
    if cost_price < 500:   return random.randint(8, 30)
    if cost_price < 1500:  return random.randint(4, 15)
    if cost_price < 5000:  return random.randint(2, 6)
    return random.randint(1, 3)


def get_store_id():
    r = supabase.table("stores").select("id").eq("name", STORE_NAME).single().execute()
    if not r.data:
        raise ValueError(f"Store '{STORE_NAME}' not found.")
    return r.data["id"]


def wipe_purchases(store_id):
    print("  Deleting existing purchase_items + purchases for this store...")
    existing = supabase.table("purchases").select("id").eq("store_id", store_id).execute().data or []
    ids = [p["id"] for p in existing]
    if ids:
        for i in range(0, len(ids), 100):
            batch = ids[i:i+100]
            supabase.table("purchase_items").delete().in_("purchase_id", batch).execute()
        supabase.table("purchases").delete().eq("store_id", store_id).execute()
    print(f"  Deleted {len(ids)} old purchase bills")


def batch_insert(table, rows, size=50):
    for i in range(0, len(rows), size):
        supabase.table(table).insert(rows[i:i+size]).execute()


def generate_purchases(store_id, prod_list, supplier_weights, target=300):
    print(f"  Generating {target} purchase bills with corrected quantities...")
    suppliers, weights = zip(*supplier_weights)
    span_days = (END_DATE - START_DATE).days
    count = 0

    for _ in range(target):
        offset = random.randint(0, span_days)
        if random.random() < 0.30:
            restock_start = date(2025, 8, 1)
            offset = (restock_start - START_DATE).days + random.randint(0, 45)
        pdate = START_DATE + timedelta(days=min(offset, span_days))

        supplier_id = random.choices(suppliers, weights=weights, k=1)[0]
        n_items = random.randint(3, 10)
        selected = random.sample(prod_list, min(n_items, len(prod_list)))

        items = []
        gross_subtotal = 0
        net_subtotal = 0
        for prod in selected:
            qty = tiered_qty(prod["cost_price"])
            unit_price = prod["cost_price"] * random.uniform(0.95, 1.05)
            disc_pct = random.choice([0, 0, 15, 20, 28, 35, 41, 48])
            net_price = unit_price * (1 - disc_pct/100)
            gross_subtotal += qty * unit_price
            net_subtotal += qty * net_price
            items.append({
                "product_id": prod["id"], "product_name": prod["name"],
                "quantity": qty, "unit_price": round(unit_price, 2),
                "discount_percent": disc_pct,
                "total": round(qty * net_price, 2),
            })

        discount_total = round(gross_subtotal - net_subtotal, 2)
        tax = round(net_subtotal * 0.13, 2)
        total = round(net_subtotal + tax, 2)

        pur = supabase.table("purchases").insert({
            "store_id": store_id, "supplier_id": supplier_id,
            "bill_number": f"BILL-{pdate.strftime('%Y%m')}-{count:04d}",
            "purchase_date": str(pdate),
            "subtotal": round(net_subtotal, 2), "discount_total": discount_total,
            "tax": tax, "total": total, "paid_amount": total, "status": "paid",
        }).execute().data[0]

        for item in items:
            item["purchase_id"] = pur["id"]
        batch_insert("purchase_items", items)
        count += 1

    print(f"  Created {count} purchase bills")


def main():
    print("=" * 60)
    print("Fixing purchase quantities - Bijeta Auto Parts")
    print("=" * 60)

    store_id = get_store_id()
    print(f"Store ID: {store_id}")

    print("\nFetching existing products + suppliers (not re-creating them)...")
    products = supabase.table("products").select("id,name,cost_price").eq("store_id", store_id).execute().data
    print(f"  {len(products)} products found")

    supplier_rows = supabase.table("suppliers").select("id,name").eq("store_id", store_id).execute().data
    name_to_id = {r["name"]: r["id"] for r in supplier_rows}
    weight_map = dict(SUPPLIERS_WEIGHTS)
    supplier_weights = [(name_to_id[n], w) for n, w in weight_map.items() if n in name_to_id]
    print(f"  {len(supplier_weights)} suppliers found")

    print("\nWiping old (oversized) purchase data...")
    wipe_purchases(store_id)

    print("\nGenerating corrected purchase bills...")
    generate_purchases(store_id, products, supplier_weights, target=300)

    total = supabase.table("purchases").select("total").eq("store_id", store_id).execute().data
    grand_total = sum(r["total"] for r in total)
    print("\n" + "=" * 60)
    print(f"Done. New total purchase value: Rs {grand_total:,.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
