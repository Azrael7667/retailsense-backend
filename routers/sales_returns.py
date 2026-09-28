from fastapi import APIRouter, Depends, HTTPException
from schemas.sales_return import SalesReturnCreate
from middleware.auth_middleware import get_current_user, get_active_store_id
from database import get_supabase
from utils.doc_numbers import next_doc_number, creator_name

router = APIRouter()

@router.get("/")
async def list_sales_returns(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    return supabase.table("sales_returns") \
        .select("*, customers(name)") \
        .eq("store_id", store_id) \
        .order("return_date", desc=True) \
        .execute().data

@router.get("/{return_id}")
async def get_sales_return(return_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    ret = supabase.table("sales_returns").select("*, customers(name, phone)") \
        .eq("id", return_id).eq("store_id", store_id).single().execute()
    if not ret.data:
        raise HTTPException(status_code=404, detail="Sales return not found")
    items = supabase.table("sales_return_items").select("*").eq("return_id", return_id).execute()
    return {**ret.data, "items": items.data}

@router.post("/")
async def create_sales_return(body: SalesReturnCreate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)

    invoice = supabase.table("invoices").select("*") \
        .eq("id", str(body.invoice_id)).eq("store_id", store_id).single().execute()
    if not invoice.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    inv = invoice.data

    invoice_items = supabase.table("invoice_items").select("*") \
        .eq("invoice_id", str(body.invoice_id)).execute().data or []
    sold_by_product = {row["product_id"]: row for row in invoice_items if row["product_id"]}
    sold_by_item_id = {row["id"]: row for row in invoice_items}

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

        pid = sold_row["product_id"]
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

    prior_credit_applied = 0.0
    if prior_return_ids:
        prior_credit_rows = supabase.table("sales_returns").select("credit_applied_amount") \
            .in_("id", prior_return_ids).execute().data or []
        prior_credit_applied = sum(r["credit_applied_amount"] or 0 for r in prior_credit_rows)

    unpaid_on_invoice = round(inv["total"] - (inv["paid_amount"] or 0) - prior_credit_applied, 2)
    unpaid_on_invoice = max(unpaid_on_invoice, 0.0)

    credit_applied_amount = round(min(unpaid_on_invoice, total_refund_amount), 2)
    cash_refunded_amount = round(total_refund_amount - credit_applied_amount, 2)

    return_number = next_doc_number(store_id, "sales_return")

    remarks = f"({', '.join(remark_names)})"

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
        "created_by_name": creator_name(user),
    }
    sales_return = supabase.table("sales_returns").insert(return_data).execute().data[0]

    for item in return_items:
        item["return_id"] = sales_return["id"]
    supabase.table("sales_return_items").insert(return_items).execute()

    if inv.get("customer_id") and credit_applied_amount > 0:
        cust = supabase.table("customers").select("balance").eq("id", inv["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) - credit_applied_amount, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", inv["customer_id"]).execute()

    for item in return_items:
        if item["restock_flag"] and item["product_id"]:
            prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] + item["quantity_returned"]
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    return {**sales_return, "items": return_items}


@router.delete("/{return_id}")
async def delete_sales_return(return_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)

    ret = supabase.table("sales_returns").select("*") \
        .eq("id", return_id).eq("store_id", store_id).single().execute()
    if not ret.data:
        raise HTTPException(status_code=404, detail="Sales return not found")
    r = ret.data

    items = supabase.table("sales_return_items").select("*").eq("return_id", return_id).execute().data or []

    for item in items:
        if item["restock_flag"] and item["product_id"]:
            prod = supabase.table("products").select("stock_quantity").eq("id", item["product_id"]).single().execute()
            if prod.data:
                new_qty = prod.data["stock_quantity"] - item["quantity_returned"]
                supabase.table("products").update({"stock_quantity": new_qty}).eq("id", item["product_id"]).execute()

    if r.get("customer_id") and r["credit_applied_amount"] > 0:
        cust = supabase.table("customers").select("balance").eq("id", r["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) + r["credit_applied_amount"], 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", r["customer_id"]).execute()

    supabase.table("sales_return_items").delete().eq("return_id", return_id).execute()
    supabase.table("sales_returns").delete().eq("id", return_id).execute()

    return {"message": "Sales return deleted"}
