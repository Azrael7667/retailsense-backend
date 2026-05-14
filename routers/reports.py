from fastapi import APIRouter, Depends
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from datetime import date
from typing import Optional

router = APIRouter()

@router.get("/profit-loss")
async def profit_loss(
    start_date: date,
    end_date: date,
    user=Depends(get_current_user)
):
    supabase = get_supabase()
    store_id = get_store_id(user.id)

    invoices = supabase.table("invoices").select("total").eq("store_id", store_id).eq("status", "paid").gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data
    purchases = supabase.table("purchases").select("total").eq("store_id", store_id).gte("purchase_date", str(start_date)).lte("purchase_date", str(end_date)).execute().data
    expenses = supabase.table("expenses").select("amount").eq("store_id", store_id).gte("expense_date", str(start_date)).lte("expense_date", str(end_date)).execute().data

    total_revenue  = sum(i["total"] for i in invoices)
    total_purchase = sum(p["total"] for p in purchases)
    total_expenses = sum(e["amount"] for e in expenses)
    gross_profit   = total_revenue - total_purchase
    net_profit     = gross_profit - total_expenses

    return {
        "period": {"start": str(start_date), "end": str(end_date)},
        "revenue":        round(total_revenue, 2),
        "cost_of_goods":  round(total_purchase, 2),
        "gross_profit":   round(gross_profit, 2),
        "expenses":       round(total_expenses, 2),
        "net_profit":     round(net_profit, 2),
        "gross_margin":   round((gross_profit / total_revenue * 100) if total_revenue else 0, 2),
    }

@router.get("/sales-summary")
async def sales_summary(
    start_date: date,
    end_date: date,
    user=Depends(get_current_user)
):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    invoices = supabase.table("invoices").select("invoice_date, total, status").eq("store_id", store_id).gte("invoice_date", str(start_date)).lte("invoice_date", str(end_date)).execute().data
    return {"invoices": invoices, "total": round(sum(i["total"] for i in invoices), 2), "count": len(invoices)}

@router.get("/top-products")
async def top_products(limit: int = 10, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    items = supabase.table("invoice_items").select("product_name, quantity, total").execute().data
    from collections import defaultdict
    agg = defaultdict(lambda: {"quantity": 0, "revenue": 0})
    for item in items:
        agg[item["product_name"]]["quantity"] += item["quantity"]
        agg[item["product_name"]]["revenue"]  += item["total"]
    sorted_products = sorted(agg.items(), key=lambda x: x[1]["revenue"], reverse=True)[:limit]
    return [{"product": k, **v} for k, v in sorted_products]
