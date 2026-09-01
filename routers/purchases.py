from fastapi import APIRouter, Depends
from schemas.purchase import PurchaseCreate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from datetime import date
from typing import Optional

router = APIRouter()

@router.get("/")
async def list_purchases(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user=Depends(get_current_user)
):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    q = supabase.table("purchases").select("*, suppliers(name)").eq("store_id", store_id).order("purchase_date", desc=True)
    if start_date:
        q = q.gte("purchase_date", str(start_date))
    if end_date:
        q = q.lte("purchase_date", str(end_date))
    return q.execute().data

def _net_price(unit_price: float, discount_percent: float) -> float:
    disc = discount_percent or 0
    return round(unit_price * (1 - disc / 100.0), 4)

@router.post("/")
async def create_purchase(body: PurchaseCreate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)

    gross_subtotal = sum(item.quantity * item.unit_price for item in body.items)
    subtotal       = sum(item.quantity * _net_price(item.unit_price, item.discount_percent) for item in body.items)
    discount_total = round(gross_subtotal - subtotal, 2)
    total          = subtotal + body.tax

    purchase_data = {
        "store_id": store_id,
        "supplier_id": str(body.supplier_id) if body.supplier_id else None,
        "bill_number": body.bill_number,
        "purchase_date": str(body.purchase_date),
        "subtotal": round(subtotal, 2),
        "discount_total": discount_total,
        "tax": body.tax,
        "total": round(total, 2),
        "paid_amount": round(total, 2),
        "status": "paid",
        "notes": body.notes,
    }
    purchase = supabase.table("purchases").insert(purchase_data).execute().data[0]

    line_items = []
    for item in body.items:
        net_price = _net_price(item.unit_price, item.discount_percent)
        line_items.append({
            "purchase_id": purchase["id"],
            "product_id": str(item.product_id) if item.product_id else None,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "discount_percent": item.discount_percent or 0,
            "total": round(item.quantity * net_price, 2),
        })
    supabase.table("purchase_items").insert(line_items).execute()

    # Add stock + roll cost price forward (keep previous cost for reference)
    for item in body.items:
        if item.product_id:
            prod = supabase.table("products").select("stock_quantity, cost_price").eq("id", str(item.product_id)).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] + item.quantity
                net_price = _net_price(item.unit_price, item.discount_percent)
                supabase.table("products").update({
                    "stock_quantity": new_qty,
                    "previous_cost_price": prod.data["cost_price"],
                    "cost_price": net_price,
                    "list_price": item.unit_price,
                }).eq("id", str(item.product_id)).execute()

    return purchase
