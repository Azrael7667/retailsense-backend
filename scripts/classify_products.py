"""
RetailSense Nepal - Product Velocity Classifier
Automatically classifies products as Fast or Slow moving
based on actual sales data from invoice_items

Run: python scripts/classify_products.py
Or:  called via FastAPI endpoint
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from supabase import create_client
from collections import defaultdict
from datetime import datetime, date
import statistics

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

STORE_NAME = "Bijeta Auto Parts"


def get_store_id():
    result = supabase.table("stores").select("id") \
        .eq("name", STORE_NAME).single().execute()
    if not result.data:
        raise ValueError(f"Store '{STORE_NAME}' not found")
    return result.data["id"]


def fetch_all_invoice_items(store_id: str):
    """Fetch all invoice items with their invoice dates — paginated"""
    print("  Fetching invoice items...")
    all_items = []
    page = 0
    while True:
        result = supabase.table("invoice_items") \
            .select("product_id, product_name, quantity, invoice_id, invoices(invoice_date, store_id)") \
            .range(page * 1000, (page + 1) * 1000 - 1) \
            .execute()
        # Filter by store
        for item in result.data:
            if item.get("invoices") and item["invoices"].get("store_id") == store_id:
                all_items.append(item)
        if len(result.data) < 1000:
            break
        page += 1
    print(f"  Fetched {len(all_items)} invoice items")
    return all_items


def fetch_all_products(store_id: str):
    """Fetch all active products"""
    result = supabase.table("products") \
        .select("id, name, product_type, reorder_level") \
        .eq("store_id", store_id) \
        .eq("is_active", True) \
        .execute()
    return result.data or []


def calculate_velocity(items: list, products: list) -> dict:
    """
    Calculate sales velocity per product
    Velocity = average units sold per month

    Returns dict: { product_id: { velocity, total_qty, transactions, months_active } }
    """
    print("  Calculating sales velocity...")

    # Group by product
    product_sales = defaultdict(lambda: {
        "total_qty": 0,
        "transactions": 0,
        "dates": [],
        "name": "",
    })

    for item in items:
        pid  = item.get("product_id")
        date_str = item.get("invoices", {}).get("invoice_date", "")
        if not pid or not date_str:
            continue

        product_sales[pid]["total_qty"]    += float(item.get("quantity", 0))
        product_sales[pid]["transactions"] += 1
        product_sales[pid]["name"]          = item.get("product_name", "")
        product_sales[pid]["dates"].append(date_str)

    # Calculate monthly velocity for each product
    velocity_map = {}
    for pid, data in product_sales.items():
        if not data["dates"]:
            continue

        dates = sorted(data["dates"])
        first = datetime.strptime(dates[0],  "%Y-%m-%d").date()
        last  = datetime.strptime(dates[-1], "%Y-%m-%d").date()

        # Months active (minimum 1 to avoid division by zero)
        months_active = max(1, ((last - first).days / 30.44))

        # Velocity = avg units per month
        velocity = data["total_qty"] / months_active

        velocity_map[pid] = {
            "velocity":       round(velocity, 2),
            "total_qty":      data["total_qty"],
            "transactions":   data["transactions"],
            "months_active":  round(months_active, 1),
            "name":           data["name"],
        }

    return velocity_map


def classify(velocity_map: dict, products: list) -> list:
    """
    Classify each product as fast or slow based on
    median velocity of the store's product portfolio

    Products with no sales history → slow (conservative)
    Products above median → fast
    Products below or equal to median → slow
    """
    print("  Classifying products...")

    velocities = [v["velocity"] for v in velocity_map.values() if v["velocity"] > 0]

    if not velocities:
        print("  No sales data found — all products set to slow")
        median_velocity = 0
    else:
        median_velocity = statistics.median(velocities)
        print(f"  Median velocity: {median_velocity:.2f} units/month")
        print(f"  Products with sales data: {len(velocities)}")

    results = []
    for product in products:
        pid  = product["id"]
        data = velocity_map.get(pid)

        if not data or data["velocity"] == 0:
            # No sales history → slow moving
            classification = "slow"
            velocity       = 0.0
            reorder_level  = max(product.get("reorder_level", 2), 2)
        elif data["velocity"] > median_velocity:
            # Above median → fast moving
            classification = "fast"
            velocity       = data["velocity"]
            # Reorder level = 2 weeks of sales
            reorder_level  = max(2, round(data["velocity"] * 0.5))
        else:
            # Below or equal to median → slow moving
            classification = "slow"
            velocity       = data["velocity"]
            # Reorder level = 1 month of sales
            reorder_level  = max(1, round(data["velocity"] * 0.25))

        results.append({
            "product_id":    pid,
            "product_name":  data["name"] if data else product.get("name", ""),
            "product_type":  classification,
            "velocity":      velocity,
            "reorder_level": int(reorder_level),
            "current_type":  product.get("product_type", "slow"),
        })

    return results


def update_products(results: list, dry_run: bool = False) -> dict:
    """Update product_type and reorder_level in Supabase"""
    if dry_run:
        print("  [DRY RUN] No changes saved")
        return {"updated": 0, "fast": 0, "slow": 0}

    print("  Updating products in database...")
    fast_count = 0
    slow_count = 0
    updated    = 0

    for r in results:
        supabase.table("products").update({
            "product_type":  r["product_type"],
            "reorder_level": r["reorder_level"],
        }).eq("id", r["product_id"]).execute()
        updated += 1
        if r["product_type"] == "fast":
            fast_count += 1
        else:
            slow_count += 1

    return {"updated": updated, "fast": fast_count, "slow": slow_count}


def classify_products(store_id: str = None, dry_run: bool = False):
    if not store_id:
        store_id = get_store_id()

    print(f"\nProduct Velocity Classification")
    print(f"Store: {STORE_NAME}")
    print("-" * 50)

    items    = fetch_all_invoice_items(store_id)
    products = fetch_all_products(store_id)

    print(f"  Products to classify: {len(products)}")

    velocity_map = calculate_velocity(items, products)
    results      = classify(velocity_map, products)

    # Sort by velocity descending for display
    results_sorted = sorted(results, key=lambda x: x["velocity"], reverse=True)

    print(f"\n  Top 10 fastest moving products:")
    print(f"  {'Product':<40} {'Velocity':>10} {'Type':<8} {'Reorder'}")
    print("  " + "-" * 70)
    for r in results_sorted[:10]:
        changed = " ← changed" if r["product_type"] != r["current_type"] else ""
        print(f"  {r['product_name'][:40]:<40} "
              f"{r['velocity']:>8.1f}/mo "
              f"{r['product_type']:<8} "
              f"{r['reorder_level']}{changed}")

    print(f"\n  Bottom 10 slowest moving products:")
    print(f"  {'Product':<40} {'Velocity':>10} {'Type':<8} {'Reorder'}")
    print("  " + "-" * 70)
    for r in results_sorted[-10:]:
        print(f"  {r['product_name'][:40]:<40} "
              f"{r['velocity']:>8.1f}/mo "
              f"{r['product_type']:<8} "
              f"{r['reorder_level']}")

    # Summary
    fast = sum(1 for r in results if r["product_type"] == "fast")
    slow = sum(1 for r in results if r["product_type"] == "slow")
    no_sales = sum(1 for r in results if r["velocity"] == 0)

    print(f"\n  Classification Summary:")
    print(f"  Fast moving: {fast} products")
    print(f"  Slow moving: {slow} products")
    print(f"  No sales history: {no_sales} products")

    # Update database
    stats = update_products(results, dry_run=dry_run)
    if not dry_run:
        print(f"\n  Updated {stats['updated']} products in database")

    print("\n  Done!")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()
    classify_products(dry_run=args.dry_run)
