"""
📁 BACKEND — routers/classification.py
Endpoints for product velocity and customer pricing-tier classification
"""

from fastapi import APIRouter, Depends
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
import os
import sys

router = APIRouter()

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"


@router.get("/classify-products/preview")
async def preview_classification():
    """Preview product classification without saving"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from scripts.classify_products import (
        classify_products as cp,
        get_store_id as get_store_id_by_name,
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


@router.post("/classify-customers/preview")
async def preview_customer_classification(user=Depends(get_current_user)):
    """Preview customer pricing-tier classification without saving"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from scripts.classify_customers import classify_customers

    store_id = get_store_id(user.id)
    outcome = classify_customers(store_id=store_id, dry_run=True)

    return {
        "stats": outcome["stats"],
        "customers": [
            {
                "id": r["id"],
                "name": r["name"],
                "rfm_score": r["rfm_score"],
                "suggested_tier": r["suggested_tier"],
                "suggested_discount": r["suggested_discount"],
            }
            for r in sorted(outcome["results"], key=lambda x: x["rfm_score"], reverse=True)
        ],
    }


@router.post("/classify-customers/apply")
async def apply_customer_classification(user=Depends(get_current_user)):
    """Run customer pricing-tier classification and save results"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from scripts.classify_customers import classify_customers

    store_id = get_store_id(user.id)
    outcome = classify_customers(store_id=store_id, dry_run=False)

    return {"stats": outcome["stats"]}
