"""
📁 BACKEND — routers/classification.py
Endpoint to trigger product velocity classification
"""

from fastapi import APIRouter, BackgroundTasks
import os
import sys

router = APIRouter()

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"


@router.post("/classify-products")
async def classify_products(background_tasks: BackgroundTasks):
    """Trigger product velocity classification in background"""
    def run():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from scripts.classify_products import classify_products
        classify_products(store_id=STORE_ID)

    background_tasks.add_task(run)
    return {
        "status":  "started",
        "message": "Product classification started. Products will be updated in ~10 seconds."
    }


@router.get("/classify-products/preview")
async def preview_classification():
    """Preview classification without saving"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from scripts.classify_products import (
        classify_products as cp,
        get_store_id,
        fetch_all_invoice_items,
        fetch_all_products,
        calculate_velocity,
        classify
    )

    store_id     = STORE_ID
    items        = fetch_all_invoice_items(store_id)
    products     = fetch_all_products(store_id)
    velocity_map = calculate_velocity(items, products)
    results      = classify(velocity_map, products)

    fast     = [r for r in results if r["product_type"] == "fast"]
    slow     = [r for r in results if r["product_type"] == "slow"]
    no_sales = [r for r in results if r["velocity"] == 0]

    return {
        "total_products": len(results),
        "fast_count":     len(fast),
        "slow_count":     len(slow),
        "no_sales_count": len(no_sales),
        "top_fast": sorted(fast, key=lambda x: x["velocity"], reverse=True)[:10],
        "top_slow": sorted([r for r in slow if r["velocity"] > 0], key=lambda x: x["velocity"])[:10],
        "no_sales_products": [r["product_name"] for r in no_sales],
    }
