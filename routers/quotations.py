from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from database import get_supabase
from middleware.auth_middleware import get_active_store_id, get_current_user
from routers.invoices import _generate_sequential_invoice_number
from schemas.quotation import QuotationConvert, QuotationCreate, QuotationStatusUpdate
from utils.doc_numbers import next_doc_number

router = APIRouter()


def _unique_number(supabase, store_id: str) -> str:
    for _ in range(5):
        number = next_doc_number(store_id, "quotation")
        dup = (supabase.table("quotations").select("id")
               .eq("store_id", store_id).eq("quotation_number", number).limit(1).execute())
        if not dup.data:
            return number
    raise HTTPException(status_code=500, detail="Could not generate a unique quotation number")


def _totals(body: QuotationCreate):
    subtotal = sum((i.quantity * i.unit_price) - i.discount for i in body.items)
    total = round(subtotal - body.discount + body.tax, 2)
    return round(subtotal, 2), total


def _item_rows(quotation_id: str, body: QuotationCreate):
    return [{
        "quotation_id": quotation_id,
        "product_id": str(i.product_id) if i.product_id else None,
        "product_name": i.product_name,
        "quantity": i.quantity,
        "unit_price": i.unit_price,
        "discount": i.discount,
        "total": round((i.quantity * i.unit_price) - i.discount, 2),
    } for i in body.items]


def _get_owned(supabase, quotation_id: str, store_id: str) -> dict:
    res = (supabase.table("quotations").select("*")
           .eq("id", quotation_id).eq("store_id", store_id).limit(1).execute())
    if not res.data:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return res.data[0]


@router.get("/")
async def list_quotations(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    user=Depends(get_current_user),
    store_id: str = Depends(get_active_store_id),
):
    supabase = get_supabase(user.access_token)
    q = (supabase.table("quotations").select("*, customers(name)")
         .eq("store_id", store_id).order("quotation_date", desc=True))
    if start_date:
        q = q.gte("quotation_date", str(start_date))
    if end_date:
        q = q.lte("quotation_date", str(end_date))
    if status:
        q = q.eq("status", status)
    rows = q.execute().data or []
    today = str(date.today())
    for r in rows:   # show overdue open quotations as expired without a cron job
        if r["status"] in ("draft", "sent") and r.get("valid_until") and r["valid_until"] < today:
            r["status"] = "expired"
    return rows


@router.get("/{quotation_id}")
async def get_quotation(quotation_id: str, user=Depends(get_current_user),
                        store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    row = _get_owned(supabase, quotation_id, store_id)
    cust = None
    if row.get("customer_id"):
        c = (supabase.table("customers").select("name, phone, address")
             .eq("id", row["customer_id"]).limit(1).execute().data)
        cust = c[0] if c else None
    items = supabase.table("quotation_items").select("*").eq("quotation_id", quotation_id).execute().data
    return {**row, "customers": cust, "items": items or []}


@router.post("/")
async def create_quotation(body: QuotationCreate, user=Depends(get_current_user),
                           store_id: str = Depends(get_active_store_id)):
    if not body.items:
        raise HTTPException(status_code=400, detail="Add at least one item")
    supabase = get_supabase(user.access_token)

    if body.quotation_number_mode == "manual":
        number = (body.quotation_number or "").strip()
        if not number:
            raise HTTPException(status_code=400, detail="Enter a quotation number, or switch back to Auto")
        dup = (supabase.table("quotations").select("id")
               .eq("store_id", store_id).eq("quotation_number", number).limit(1).execute())
        if dup.data:
            raise HTTPException(status_code=400, detail=f'Quotation number "{number}" already exists')
    else:
        number = _unique_number(supabase, store_id)

    subtotal, total = _totals(body)
    row = supabase.table("quotations").insert({
        "store_id": store_id,
        "customer_id": str(body.customer_id) if body.customer_id else None,
        "quotation_number": number,
        "quotation_date": str(body.quotation_date),
        "valid_until": str(body.valid_until) if body.valid_until else None,
        "subtotal": subtotal,
        "discount": body.discount,
        "tax": body.tax,
        "total": total,
        "status": "draft",
        "notes": body.notes,
        "terms": body.terms,
    }).execute().data[0]
    supabase.table("quotation_items").insert(_item_rows(row["id"], body)).execute()
    return row


@router.put("/{quotation_id}")
async def update_quotation(quotation_id: str, body: QuotationCreate,
                           user=Depends(get_current_user),
                           store_id: str = Depends(get_active_store_id)):
    if not body.items:
        raise HTTPException(status_code=400, detail="Add at least one item")
    supabase = get_supabase(user.access_token)
    old = _get_owned(supabase, quotation_id, store_id)
    if old["status"] == "converted":
        raise HTTPException(status_code=400, detail="A converted quotation can't be edited")

    subtotal, total = _totals(body)
    updated = supabase.table("quotations").update({
        "customer_id": str(body.customer_id) if body.customer_id else None,
        "quotation_date": str(body.quotation_date),
        "valid_until": str(body.valid_until) if body.valid_until else None,
        "subtotal": subtotal,
        "discount": body.discount,
        "tax": body.tax,
        "total": total,
        "notes": body.notes,
        "terms": body.terms,
    }).eq("id", quotation_id).execute().data[0]
    supabase.table("quotation_items").delete().eq("quotation_id", quotation_id).execute()
    supabase.table("quotation_items").insert(_item_rows(quotation_id, body)).execute()
    return updated


@router.patch("/{quotation_id}/status")
async def set_status(quotation_id: str, body: QuotationStatusUpdate,
                     user=Depends(get_current_user),
                     store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    old = _get_owned(supabase, quotation_id, store_id)
    if old["status"] == "converted":
        raise HTTPException(status_code=400, detail="A converted quotation can't change status")
    if body.status == "converted":
        raise HTTPException(status_code=400, detail="Use the convert action instead")
    return (supabase.table("quotations").update({"status": body.status})
            .eq("id", quotation_id).execute().data[0])


@router.delete("/{quotation_id}")
async def delete_quotation(quotation_id: str, user=Depends(get_current_user),
                           store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    old = _get_owned(supabase, quotation_id, store_id)
    if old["status"] == "converted":
        raise HTTPException(status_code=400, detail="A converted quotation can't be deleted")
    supabase.table("quotation_items").delete().eq("quotation_id", quotation_id).execute()
    supabase.table("quotations").delete().eq("id", quotation_id).execute()
    return {"message": "Quotation deleted"}


# ------------------------------------------------ convert to invoice
@router.post("/{quotation_id}/convert")
async def convert_to_invoice(quotation_id: str, body: QuotationConvert,
                             user=Depends(get_current_user),
                             store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    q = _get_owned(supabase, quotation_id, store_id)
    if q["status"] == "converted":
        raise HTTPException(status_code=400, detail="This quotation was already converted")
    if q["status"] == "rejected":
        raise HTTPException(status_code=400, detail="A rejected quotation can't be converted")

    items = supabase.table("quotation_items").select("*").eq("quotation_id", quotation_id).execute().data or []
    if not items:
        raise HTTPException(status_code=400, detail="This quotation has no items")

    total = round(float(q["total"]), 2)
    paid_amount = round(min(max(body.paid_amount or 0.0, 0.0), total), 2)
    if paid_amount <= 0:
        inv_status = "unpaid"
    elif paid_amount < total:
        inv_status = "partial"
    else:
        inv_status = "paid"

    invoice = supabase.table("invoices").insert({
        "store_id": store_id,
        "customer_id": q.get("customer_id"),
        "invoice_number": _generate_sequential_invoice_number(supabase, store_id),
        "invoice_date": str(body.invoice_date or date.today()),
        "subtotal": q["subtotal"],
        "discount": q["discount"],
        "tax": q["tax"],
        "delivery_charge": 0,
        "delivery_address": None,
        "delivery_note": None,
        "total": total,
        "paid_amount": paid_amount,
        "payment_method": body.payment_method,
        "status": inv_status,
        "notes": q.get("notes"),
    }).execute().data[0]

    unpaid = round(total - paid_amount, 2)
    if q.get("customer_id") and unpaid > 0:
        cust = supabase.table("customers").select("balance").eq("id", q["customer_id"]).single().execute()
        if cust.data:
            supabase.table("customers").update(
                {"balance": round((cust.data["balance"] or 0) + unpaid, 2)}
            ).eq("id", q["customer_id"]).execute()

    pids = list({i["product_id"] for i in items if i.get("product_id")})
    cost_map = {}
    if pids:
        rows = supabase.table("products").select("id, cost_price").in_("id", pids).execute().data or []
        cost_map = {r["id"]: r.get("cost_price") or 0 for r in rows}

    supabase.table("invoice_items").insert([{
        "invoice_id": invoice["id"],
        "product_id": i.get("product_id"),
        "product_name": i["product_name"],
        "quantity": i["quantity"],
        "unit_price": i["unit_price"],
        "discount": i["discount"],
        "total": i["total"],
        "cost_price_at_sale": cost_map.get(i.get("product_id"), 0) if i.get("product_id") else 0,
    } for i in items]).execute()

    for i in items:
        if i.get("product_id"):
            prod = supabase.table("products").select("stock_quantity").eq("id", i["product_id"]).single().execute()
            if prod.data:
                supabase.table("products").update(
                    {"stock_quantity": prod.data["stock_quantity"] - i["quantity"]}
                ).eq("id", i["product_id"]).execute()

    supabase.table("quotations").update(
        {"status": "converted", "converted_invoice_id": invoice["id"]}
    ).eq("id", quotation_id).execute()
    return {"invoice": invoice, "quotation_id": quotation_id}
