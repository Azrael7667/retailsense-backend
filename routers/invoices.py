from fastapi import APIRouter, Depends, HTTPException
from schemas.invoice import InvoiceCreate
from middleware.auth_middleware import get_current_user, get_active_store_id
from database import get_supabase
from utils.doc_numbers import next_doc_number
from datetime import date
from typing import Optional

router = APIRouter()

def _generate_sequential_invoice_number(supabase, store_id: str) -> str:
    # Skip any number already taken (e.g. someone typed it manually)
    for _ in range(5):
        number = next_doc_number(store_id, "invoice")
        dup = supabase.table("invoices").select("id").eq("store_id", store_id).eq("invoice_number", number).limit(1).execute()
        if not dup.data:
            return number
    raise HTTPException(status_code=500, detail="Could not generate a unique invoice number")

@router.get("/")
async def list_invoices(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    user=Depends(get_current_user),
    store_id: str = Depends(get_active_store_id)
):
    supabase = get_supabase(user.access_token)
    q = supabase.table("invoices").select("*, customers(name)").eq("store_id", store_id).order("invoice_date", desc=True)
    if start_date:
        q = q.gte("invoice_date", str(start_date))
    if end_date:
        q = q.lte("invoice_date", str(end_date))
    if status:
        q = q.eq("status", status)
    return q.execute().data

@router.post("/")
async def create_invoice(body: InvoiceCreate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)

    if body.invoice_number_mode == "manual":
        if not body.invoice_number or not body.invoice_number.strip():
            raise HTTPException(status_code=400, detail="Enter an invoice number, or switch back to Auto")
        invoice_number = body.invoice_number.strip()
        dup = supabase.table("invoices").select("id").eq("store_id", store_id).eq("invoice_number", invoice_number).limit(1).execute()
        if dup.data:
            raise HTTPException(status_code=400, detail=f'Invoice number "{invoice_number}" already exists')
    else:
        invoice_number = _generate_sequential_invoice_number(supabase, store_id)

    subtotal = sum((item.quantity * item.unit_price) - item.discount for item in body.items)
    total = round(subtotal - body.discount + body.tax + body.delivery_charge, 2)

    paid_amount = round(min(max(body.paid_amount or 0.0, 0.0), total), 2)
    if paid_amount <= 0:
        invoice_status = "unpaid"
    elif paid_amount < total:
        invoice_status = "partial"
    else:
        invoice_status = "paid"

    invoice_data = {
        "store_id": store_id,
        "customer_id": str(body.customer_id) if body.customer_id else None,
        "invoice_number": invoice_number,
        "invoice_date": str(body.invoice_date),
        "subtotal": round(subtotal, 2),
        "discount": body.discount,
        "tax": body.tax,
        "delivery_charge": body.delivery_charge,
        "delivery_address": body.delivery_address,
        "delivery_note": body.delivery_note,
        "total": round(total, 2),
        "paid_amount": paid_amount,
        "payment_method": body.payment_method,
        "status": invoice_status,
        "notes": body.notes,
    }
    invoice = supabase.table("invoices").insert(invoice_data).execute().data[0]

    unpaid_amount = round(total - paid_amount, 2)
    if body.customer_id and unpaid_amount > 0:
        cust = supabase.table("customers").select("balance").eq("id", str(body.customer_id)).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) + unpaid_amount, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", str(body.customer_id)).execute()

    product_ids = list({str(item.product_id) for item in body.items if item.product_id})
    cost_map = {}
    if product_ids:
        rows = supabase.table("products").select("id, cost_price").in_("id", product_ids).execute().data or []
        cost_map = {r["id"]: r.get("cost_price") or 0 for r in rows}

    line_items = []
    for item in body.items:
        pid = str(item.product_id) if item.product_id else None
        line_items.append({
            "invoice_id": invoice["id"],
            "product_id": pid,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "discount": item.discount,
            "total": round((item.quantity * item.unit_price) - item.discount, 2),
            "cost_price_at_sale": cost_map.get(pid, 0) if pid else 0,
        })
    supabase.table("invoice_items").insert(line_items).execute()

    for item in body.items:
        if item.product_id:
            prod = supabase.table("products").select("stock_quantity").eq("id", str(item.product_id)).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] - item.quantity
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", str(item.product_id)).execute()

    return invoice

@router.get("/{invoice_id}")
async def get_invoice(invoice_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    invoice = supabase.table("invoices").select("*, customers(name, phone, address), stores(name, address, phone, vat_number)").eq("id", invoice_id).eq("store_id", store_id).single().execute()
    if not invoice.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    items = supabase.table("invoice_items").select("*").eq("invoice_id", invoice_id).execute()
    return {**invoice.data, "items": items.data}


@router.put("/{invoice_id}")
async def update_invoice(invoice_id: str, body: InvoiceCreate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)

    existing = supabase.table("invoices").select("*").eq("id", invoice_id).eq("store_id", store_id).single().execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    old_inv = existing.data

    existing_returns = supabase.table("sales_returns").select("id").eq("invoice_id", invoice_id).execute().data or []
    if existing_returns:
        raise HTTPException(status_code=400, detail="Cannot edit an invoice that has linked sales returns. Delete the return(s) first.")

    old_items = supabase.table("invoice_items").select("*").eq("invoice_id", invoice_id).execute().data or []

    for item in old_items:
        if item["product_id"]:
            prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] + item["quantity"]
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    old_outstanding = round(old_inv["total"] - (old_inv["paid_amount"] or 0), 2)
    if old_inv.get("customer_id") and old_outstanding != 0:
        cust = supabase.table("customers").select("balance").eq("id", old_inv["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) - old_outstanding, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", old_inv["customer_id"]).execute()

    supabase.table("invoice_items").delete().eq("invoice_id", invoice_id).execute()

    subtotal = sum((item.quantity * item.unit_price) - item.discount for item in body.items)
    total = round(subtotal - body.discount + body.tax + body.delivery_charge, 2)
    paid_amount = round(min(max(body.paid_amount or 0.0, 0.0), total), 2)
    if paid_amount <= 0:
        invoice_status = "unpaid"
    elif paid_amount < total:
        invoice_status = "partial"
    else:
        invoice_status = "paid"

    updated_fields = {
        "customer_id": str(body.customer_id) if body.customer_id else None,
        "invoice_date": str(body.invoice_date),
        "subtotal": round(subtotal, 2),
        "discount": body.discount,
        "tax": body.tax,
        "delivery_charge": body.delivery_charge,
        "delivery_address": body.delivery_address,
        "delivery_note": body.delivery_note,
        "total": round(total, 2),
        "paid_amount": paid_amount,
        "payment_method": body.payment_method,
        "status": invoice_status,
        "notes": body.notes,
    }
    updated = supabase.table("invoices").update(updated_fields).eq("id", invoice_id).execute().data[0]

    new_unpaid = round(total - paid_amount, 2)
    if body.customer_id and new_unpaid > 0:
        cust = supabase.table("customers").select("balance").eq("id", str(body.customer_id)).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) + new_unpaid, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", str(body.customer_id)).execute()

    product_ids = list({str(item.product_id) for item in body.items if item.product_id})
    cost_map = {}
    if product_ids:
        rows = supabase.table("products").select("id, cost_price").in_("id", product_ids).execute().data or []
        cost_map = {r["id"]: r.get("cost_price") or 0 for r in rows}

    line_items = []
    for item in body.items:
        pid = str(item.product_id) if item.product_id else None
        line_items.append({
            "invoice_id": invoice_id,
            "product_id": pid,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "discount": item.discount,
            "total": round((item.quantity * item.unit_price) - item.discount, 2),
            "cost_price_at_sale": cost_map.get(pid, 0) if pid else 0,
        })
    supabase.table("invoice_items").insert(line_items).execute()

    for item in body.items:
        if item.product_id:
            prod = supabase.table("products").select("stock_quantity").eq("id", str(item.product_id)).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] - item.quantity
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", str(item.product_id)).execute()

    return {**updated, "items": line_items}


@router.delete("/{invoice_id}")
async def delete_invoice(invoice_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)

    invoice = supabase.table("invoices").select("*").eq("id", invoice_id).eq("store_id", store_id).single().execute()
    if not invoice.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    inv = invoice.data

    invoice_items = supabase.table("invoice_items").select("*").eq("invoice_id", invoice_id).execute().data or []

    returns = supabase.table("sales_returns").select("*").eq("invoice_id", invoice_id).execute().data or []
    total_credit_applied = 0.0
    restocked_by_product = {}

    for ret in returns:
        ret_items = supabase.table("sales_return_items").select("*").eq("return_id", ret["id"]).execute().data or []
        for ri in ret_items:
            if ri["restock_flag"] and ri["product_id"]:
                restocked_by_product[ri["product_id"]] = restocked_by_product.get(ri["product_id"], 0) + ri["quantity_returned"]
        total_credit_applied = round(total_credit_applied + (ret["credit_applied_amount"] or 0), 2)
        supabase.table("sales_return_items").delete().eq("return_id", ret["id"]).execute()
    if returns:
        supabase.table("sales_returns").delete().eq("invoice_id", invoice_id).execute()

    supabase.table("payment_allocations").delete().eq("invoice_id", invoice_id).execute()

    for item in invoice_items:
        if item["product_id"]:
            already_restocked = restocked_by_product.get(item["product_id"], 0)
            net_to_restore = item["quantity"] - already_restocked
            if net_to_restore != 0:
                prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
                if prod.data:
                    new_qty = prod.data["stock_quantity"] + net_to_restore
                    supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    outstanding = round(inv["total"] - (inv["paid_amount"] or 0) - total_credit_applied, 2)
    if inv.get("customer_id") and outstanding != 0:
        cust = supabase.table("customers").select("balance").eq("id", inv["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) - outstanding, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", inv["customer_id"]).execute()

    supabase.table("invoice_items").delete().eq("invoice_id", invoice_id).execute()
    supabase.table("invoices").delete().eq("id", invoice_id).execute()

    return {"message": "Invoice deleted"}
