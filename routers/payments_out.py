from fastapi import APIRouter, Depends, HTTPException
from schemas.payment_out import PaymentOutCreate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from typing import Optional

router = APIRouter()

@router.get("/")
async def list_payments_out(supplier_id: Optional[str] = None, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)
    q = supabase.table("payments_out").select("*, suppliers(name)").eq("store_id", store_id).order("payment_date", desc=True)
    if supplier_id:
        q = q.eq("supplier_id", supplier_id)
    return q.execute().data

@router.post("/")
async def create_payment_out(body: PaymentOutCreate, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    existing_count = supabase.table("payments_out").select("id", count="exact") \
        .eq("store_id", store_id).execute()
    receipt_number = str((existing_count.count or 0) + 1)

    payment_data = {
        "store_id": store_id,
        "supplier_id": str(body.supplier_id),
        "payment_date": str(body.payment_date),
        "amount": round(body.amount, 2),
        "payment_method": body.payment_method,
        "reference": body.reference,
        "notes": body.notes,
        "receipt_number": receipt_number,
        "created_by_name": user.user_metadata.get("full_name") if user.user_metadata else None,
    }
    payment_out = supabase.table("payments_out").insert(payment_data).execute().data[0]

    # Flat balance reduction — matches the reference app's behavior: a
    # payment reduces what we owe the supplier overall, without allocating
    # against or marking any specific purchase as paid/partial.
    if body.amount > 0:
        sup = supabase.table("suppliers").select("balance").eq("id", str(body.supplier_id)).single().execute()
        if sup.data:
            new_balance = round((sup.data["balance"] or 0) - body.amount, 2)
            supabase.table("suppliers").update({"balance": new_balance}).eq("id", str(body.supplier_id)).execute()

    return {**payment_out, "unallocated_amount": 0}


@router.delete("/{payment_out_id}")
async def delete_payment_out(payment_out_id: str, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    pay = supabase.table("payments_out").select("*").eq("id", payment_out_id).eq("store_id", store_id).single().execute()
    if not pay.data:
        raise HTTPException(status_code=404, detail="Payment out not found")
    p = pay.data

    allocations = supabase.table("payment_out_allocations").select("*").eq("payment_out_id", payment_out_id).execute().data or []

    if allocations:
        # Legacy payment (created before the flat-balance change) — reverse
        # via its recorded purchase allocations, exactly as before.
        total_applied = 0.0
        for alloc in allocations:
            pur = supabase.table("purchases").select("total, paid_amount").eq("id", alloc["purchase_id"]).single().execute()
            if pur.data:
                new_paid = round((pur.data["paid_amount"] or 0) - alloc["amount"], 2)
                if new_paid <= 0:
                    new_status = "unpaid"
                elif new_paid < pur.data["total"]:
                    new_status = "partial"
                else:
                    new_status = "paid"
                supabase.table("purchases").update({
                    "paid_amount": new_paid,
                    "status": new_status,
                }).eq("id", alloc["purchase_id"]).execute()
            total_applied = round(total_applied + alloc["amount"], 2)
        supabase.table("payment_out_allocations").delete().eq("payment_out_id", payment_out_id).execute()
    else:
        # Flat-balance payment (current behavior) — nothing to reverse on
        # purchases; the full amount goes straight back onto the balance.
        total_applied = p.get("amount") or 0

    if p.get("supplier_id") and total_applied > 0:
        sup = supabase.table("suppliers").select("balance").eq("id", p["supplier_id"]).single().execute()
        if sup.data:
            new_balance = round((sup.data["balance"] or 0) + total_applied, 2)
            supabase.table("suppliers").update({"balance": new_balance}).eq("id", p["supplier_id"]).execute()

    supabase.table("payments_out").delete().eq("id", payment_out_id).execute()

    return {"message": "Payment out deleted"}
