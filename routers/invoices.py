from fastapi import APIRouter, Depends, HTTPException
from schemas.invoice import InvoiceCreate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from datetime import date
from typing import Optional
import random, string

router = APIRouter()

def _generate_invoice_number() -> str:
    suffix = ''.join(random.choices(string.digits, k=5))
    return f"INV-{suffix}"

@router.get("/")
async def list_invoices(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    user=Depends(get_current_user)
):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    q = supabase.table("invoices").select("*, customers(name)").eq("store_id", store_id).order("invoice_date", desc=True)
    if start_date:
        q = q.gte("invoice_date", str(start_date))
    if end_date:
        q = q.lte("invoice_date", str(end_date))
    if status:
        q = q.eq("status", status)
    return q.execute().data

@router.post("/")
async def create_invoice(body: InvoiceCreate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)

    subtotal = sum((item.quantity * item.unit_price) - item.discount for item in body.items)
    total = subtotal - body.discount + body.tax

    invoice_data = {
        "store_id": store_id,
        "customer_id": str(body.customer_id) if body.customer_id else None,
        "invoice_number": _generate_invoice_number(),
        "invoice_date": str(body.invoice_date),
        "subtotal": round(subtotal, 2),
        "discount": body.discount,
        "tax": body.tax,
        "total": round(total, 2),
        "paid_amount": round(total, 2),
        "payment_method": body.payment_method,
        "status": "paid",
        "notes": body.notes,
    }
    invoice = supabase.table("invoices").insert(invoice_data).execute().data[0]

    # Insert line items
    line_items = []
    for item in body.items:
        line_items.append({
            "invoice_id": invoice["id"],
            "product_id": str(item.product_id) if item.product_id else None,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "discount": item.discount,
            "total": round((item.quantity * item.unit_price) - item.discount, 2),
        })
    supabase.table("invoice_items").insert(line_items).execute()

    # Deduct stock for each product
    for item in body.items:
        if item.product_id:
            prod = supabase.table("products").select("stock_quantity").eq("id", str(item.product_id)).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] - item.quantity
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", str(item.product_id)).execute()

    return invoice

@router.get("/{invoice_id}")
async def get_invoice(invoice_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    invoice = supabase.table("invoices").select("*, customers(name, phone, address)").eq("id", invoice_id).eq("store_id", store_id).single().execute()
    if not invoice.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    items = supabase.table("invoice_items").select("*").eq("invoice_id", invoice_id).execute()
    return {**invoice.data, "items": items.data}
