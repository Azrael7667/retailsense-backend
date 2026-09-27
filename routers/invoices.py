from fastapi import APIRouter, Depends, HTTPException
from schemas.invoice import InvoiceCreate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from datetime import date
from typing import Optional

router = APIRouter()

def _generate_sequential_invoice_number(supabase, store_id: str) -> str:
    # Mirrors the numbering scheme NewInvoice.jsx used client-side before this
    # migration: sequential per-store count, tagged with the CURRENT real-world
    # year (not the invoice's own date). Small race window between the count
    # read and the insert below if two invoices save at the same instant —
    # same pre-existing risk as the old client-side version, not new here.
    count_res = supabase.table("invoices").select("id", count="exact").eq("store_id", store_id).execute()
    seq = (count_res.count or 0) + 1
    year = date.today().year
    return f"INV-{year}-{str(seq).zfill(3)}"

@router.get("/")
async def list_invoices(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    user=Depends(get_current_user)
):
    supabase = get_supabase(user.access_token)
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
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

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

    # Unpaid portion of a new invoice increases what the customer owes
    unpaid_amount = round(total - paid_amount, 2)
    if body.customer_id and unpaid_amount > 0:
        cust = supabase.table("customers").select("balance").eq("id", str(body.customer_id)).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) + unpaid_amount, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", str(body.customer_id)).execute()

    # Snapshot cost_price at time of sale — this is what makes P&L
    # accurate for past periods even after later restocks change
    # products.cost_price.
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
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)
    invoice = supabase.table("invoices").select("*, customers(name, phone, address), stores(name, address, phone, vat_number)").eq("id", invoice_id).eq("store_id", store_id).single().execute()
    if not invoice.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    items = supabase.table("invoice_items").select("*").eq("invoice_id", invoice_id).execute()
    return {**invoice.data, "items": items.data}


@router.put("/{invoice_id}")
async def update_invoice(invoice_id: str, body: InvoiceCreate, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    existing = supabase.table("invoices").select("*").eq("id", invoice_id).eq("store_id", store_id).single().execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    old_inv = existing.data

    # Editing is blocked once a sales return exists against this invoice — the return's
    # quantities/refund math was computed against the ORIGINAL line items, and there's no
    # safe way to reconcile that against a changed invoice. Delete the return(s) first.
    existing_returns = supabase.table("sales_returns").select("id").eq("invoice_id", invoice_id).execute().data or []
    if existing_returns:
        raise HTTPException(status_code=400, detail="Cannot edit an invoice that has linked sales returns. Delete the return(s) first.")

    old_items = supabase.table("invoice_items").select("*").eq("invoice_id", invoice_id).execute().data or []

    # ---- 1. Reverse the OLD invoice's effects (mirrors delete_invoice, minus the
    # sales-return cascade which we've already ruled out above) ----

    # Restore stock consumed by the old line items
    for item in old_items:
        if item["product_id"]:
            prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] + item["quantity"]
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    # Reverse the old invoice's contribution to the (old) customer's balance
    old_outstanding = round(old_inv["total"] - (old_inv["paid_amount"] or 0), 2)
    if old_inv.get("customer_id") and old_outstanding != 0:
        cust = supabase.table("customers").select("balance").eq("id", old_inv["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) - old_outstanding, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", old_inv["customer_id"]).execute()

    # Drop the old line items — they'll be replaced below
    supabase.table("invoice_items").delete().eq("invoice_id", invoice_id).execute()

    # ---- 2. Apply the NEW invoice data (mirrors create_invoice). Note: invoice_number
    # is intentionally NEVER touched here — body.invoice_number_mode / invoice_number
    # are create-only fields and are ignored on edit, so the original number sticks. ----

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

    # Re-snapshot cost_price_at_sale from current product cost — same as create_invoice
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

    # Deduct stock for the new line items
    for item in body.items:
        if item.product_id:
            prod = supabase.table("products").select("stock_quantity").eq("id", str(item.product_id)).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] - item.quantity
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", str(item.product_id)).execute()

    return {**updated, "items": line_items}


@router.delete("/{invoice_id}")
async def delete_invoice(invoice_id: str, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    invoice = supabase.table("invoices").select("*").eq("id", invoice_id).eq("store_id", store_id).single().execute()
    if not invoice.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    inv = invoice.data

    invoice_items = supabase.table("invoice_items").select("*").eq("invoice_id", invoice_id).execute().data or []

    # 1. Cascade: delete this invoice's sales returns, reversing their own stock/balance effects first
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

    # 2. Cascade: remove payment allocations tied to this invoice (money already received stays
    # received — balance was already reduced when the payment was applied; only the link goes away)
    supabase.table("payment_allocations").delete().eq("invoice_id", invoice_id).execute()

    # 3. Restore stock: undo the original sale, net of anything already restocked by returns
    for item in invoice_items:
        if item["product_id"]:
            already_restocked = restocked_by_product.get(item["product_id"], 0)
            net_to_restore = item["quantity"] - already_restocked
            if net_to_restore != 0:
                prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
                if prod.data:
                    new_qty = prod.data["stock_quantity"] + net_to_restore
                    supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    # 4. Reverse this invoice's remaining contribution to customer balance
    outstanding = round(inv["total"] - (inv["paid_amount"] or 0) - total_credit_applied, 2)
    if inv.get("customer_id") and outstanding != 0:
        cust = supabase.table("customers").select("balance").eq("id", inv["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) - outstanding, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", inv["customer_id"]).execute()

    # 5. Delete line items, then the invoice itself
    supabase.table("invoice_items").delete().eq("invoice_id", invoice_id).execute()
    supabase.table("invoices").delete().eq("id", invoice_id).execute()

    return {"message": "Invoice deleted"}
