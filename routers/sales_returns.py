from fastapi import APIRouter, Depends, HTTPException
from schemas.sales_return import SalesReturnCreate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase

router = APIRouter()

@router.get("/")
async def list_sales_returns(user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)
    return supabase.table("sales_returns") \
        .select("*, customers(name)") \
        .eq("store_id", store_id) \
        .order("return_date", desc=True) \
        .execute().data

@router.get("/{return_id}")
async def get_sales_return(return_id: str, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)
    ret = supabase.table("sales_returns").select("*, customers(name, phone)") \
        .eq("id", return_id).eq("store_id", store_id).single().execute()
    if not ret.data:
        raise HTTPException(status_code=404, detail="Sales return not found")
    items = supabase.table("sales_return_items").select("*").eq("return_id", return_id).execute()
    return {**ret.data, "items": items.data}

@router.post("/")
async def create_sales_return(body: SalesReturnCreate, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    # 1. Load the invoice and confirm it belongs to this store
    invoice = supabase.table("invoices").select("*") \
        .eq("id", str(body.invoice_id)).eq("store_id", store_id).single().execute()
    if not invoice.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    inv = invoice.data

    # 2. Load the invoice's line items — source of truth for unit_price and quantity sold
    invoice_items = supabase.table("invoice_items").select("*") \
        .eq("invoice_id", str(body.invoice_id)).execute().data or []
    sold_by_product = {row["product_id"]: row for row in invoice_items if row["product_id"]}
    sold_by_item_id = {row["id"]: row for row in invoice_items}

    # 3. Load prior returns against this invoice, to enforce the not-more-than-sold constraint
    prior_returns = supabase.table("sales_returns").select("id") \
        .eq("invoice_id", str(body.invoice_id)).execute().data or []
    prior_return_ids = [r["id"] for r in prior_returns]

    already_returned_by_product = {}
    if prior_return_ids:
        prior_items = supabase.table("sales_return_items").select("product_id, quantity_returned") \
            .in_("return_id", prior_return_ids).execute().data or []
        for row in prior_items:
            pid = row["product_id"]
            already_returned_by_product[pid] = already_returned_by_product.get(pid, 0) + row["quantity_returned"]

    # 4. Validate each requested line and build return_items
    return_items = []
    total_refund_amount = 0.0
    remark_names = []

    for line in body.items:
        pid = str(line.product_id) if line.product_id else None
        sold_row = sold_by_product.get(pid) if pid else None
        if not sold_row and line.invoice_item_id:
            sold_row = sold_by_item_id.get(str(line.invoice_item_id))
        if not sold_row:
            raise HTTPException(status_code=400, detail="Item was not part of this invoice")

        pid = sold_row["product_id"]  # normalize: always the real product_id from the invoice row (may be None)
        sold_qty = sold_row["quantity"]
        already_returned = already_returned_by_product.get(pid, 0) if pid else 0
        max_returnable = round(sold_qty - already_returned, 4)

        if line.quantity_returned <= 0:
            raise HTTPException(status_code=400, detail="quantity_returned must be greater than 0")
        if line.quantity_returned > max_returnable:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot return {line.quantity_returned} of '{sold_row['product_name']}' — only {max_returnable} returnable"
            )

        unit_price = sold_row["unit_price"]
        line_refund_amount = round(line.quantity_returned * unit_price, 2)
        total_refund_amount = round(total_refund_amount + line_refund_amount, 2)

        return_items.append({
            "product_id": pid,
            "product_name": sold_row["product_name"],
            "quantity_returned": line.quantity_returned,
            "unit_price": unit_price,
            "line_refund_amount": line_refund_amount,
            "restock_flag": line.restock_flag,
        })
        remark_names.append(sold_row["product_name"])

    if total_refund_amount <= 0:
        raise HTTPException(status_code=400, detail="Return must include at least one item")

    # 5. Settlement: offset whatever's still unpaid on the invoice first (net of prior returns'
    # credit already applied), refund the remainder in cash
    prior_credit_applied = 0.0
    if prior_return_ids:
        prior_credit_rows = supabase.table("sales_returns").select("credit_applied_amount") \
            .in_("id", prior_return_ids).execute().data or []
        prior_credit_applied = sum(r["credit_applied_amount"] or 0 for r in prior_credit_rows)

    unpaid_on_invoice = round(inv["total"] - (inv["paid_amount"] or 0) - prior_credit_applied, 2)
    unpaid_on_invoice = max(unpaid_on_invoice, 0.0)

    credit_applied_amount = round(min(unpaid_on_invoice, total_refund_amount), 2)
    cash_refunded_amount = round(total_refund_amount - credit_applied_amount, 2)

    # 6. Sequential per-store return number
    existing_count = supabase.table("sales_returns").select("id", count="exact") \
        .eq("store_id", store_id).execute()
    return_number = str((existing_count.count or 0) + 1)

    remarks = f"({', '.join(remark_names)})"

    # 7. Insert the return + its items
    return_data = {
        "store_id": store_id,
        "invoice_id": str(body.invoice_id),
        "customer_id": inv.get("customer_id"),
        "return_number": return_number,
        "return_date": str(body.return_date),
        "reason": body.reason,
        "total_refund_amount": total_refund_amount,
        "credit_applied_amount": credit_applied_amount,
        "cash_refunded_amount": cash_refunded_amount,
        "remarks": remarks,
        "created_by": user.id,
    }
    sales_return = supabase.table("sales_returns").insert(return_data).execute().data[0]

    for item in return_items:
        item["return_id"] = sales_return["id"]
    supabase.table("sales_return_items").insert(return_items).execute()

    # 8. Reduce customer balance by the credited portion
    if inv.get("customer_id") and credit_applied_amount > 0:
        cust = supabase.table("customers").select("balance").eq("id", inv["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) - credit_applied_amount, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", inv["customer_id"]).execute()

    # 9. Restock products flagged as resellable
    for item in return_items:
        if item["restock_flag"] and item["product_id"]:
            prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] + item["quantity_returned"]
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    return {**sales_return, "items": return_items}


@router.delete("/{return_id}")
async def delete_sales_return(return_id: str, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    ret = supabase.table("sales_returns").select("*") \
        .eq("id", return_id).eq("store_id", store_id).single().execute()
    if not ret.data:
        raise HTTPException(status_code=404, detail="Sales return not found")
    r = ret.data

    items = supabase.table("sales_return_items").select("*").eq("return_id", return_id).execute().data or []

    # Reverse restocked quantities
    for item in items:
        if item["restock_flag"] and item["product_id"]:
            prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] - item["quantity_returned"]
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    # Reverse the balance credit
    if r.get("customer_id") and r["credit_applied_amount"] > 0:
        cust = supabase.table("customers").select("balance").eq("id", r["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) + r["credit_applied_amount"], 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", r["customer_id"]).execute()

    supabase.table("sales_return_items").delete().eq("return_id", return_id).execute()
    supabase.table("sales_returns").delete().eq("id", return_id).execute()

    return {"message": "Sales return deleted"}
