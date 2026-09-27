from fastapi import APIRouter, Depends, HTTPException
from schemas.payment import PaymentCreate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from typing import Optional

router = APIRouter()

@router.get("/")
async def list_payments(customer_id: Optional[str] = None, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)
    q = supabase.table("payments").select("*, customers(name)").eq("store_id", store_id).order("payment_date", desc=True)
    if customer_id:
        q = q.eq("customer_id", customer_id)
    return q.execute().data

@router.post("/")
async def create_payment(body: PaymentCreate, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    existing_count = supabase.table("payments").select("id", count="exact") \
        .eq("store_id", store_id).execute()
    receipt_number = str((existing_count.count or 0) + 1)

    payment_data = {
        "store_id": store_id,
        "customer_id": str(body.customer_id) if body.customer_id else None,
        "payment_date": str(body.payment_date),
        "amount": round(body.amount, 2),
        "payment_method": body.payment_method,
        "reference": body.reference,
        "notes": body.notes,
        "receipt_number": receipt_number,
        "created_by_name": user.user_metadata.get("full_name") if user.user_metadata else None,
    }
    payment = supabase.table("payments").insert(payment_data).execute().data[0]

    # Flat balance reduction — matches the reference app's behavior: a
    # payment reduces what the customer owes overall, without allocating
    # against or marking any specific invoice as paid/partial.
    if body.customer_id and body.amount > 0:
        cust = supabase.table("customers").select("balance").eq("id", str(body.customer_id)).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) - body.amount, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", str(body.customer_id)).execute()

    return {**payment, "unallocated_amount": 0}


@router.delete("/{payment_id}")
async def delete_payment(payment_id: str, user=Depends(get_current_user)):
    supabase = get_supabase(user.access_token)
    store_id = get_store_id(user.id)

    pay = supabase.table("payments").select("*") \
        .eq("id", payment_id).eq("store_id", store_id).single().execute()
    if not pay.data:
        raise HTTPException(status_code=404, detail="Payment not found")
    p = pay.data

    allocations = supabase.table("payment_allocations").select("*").eq("payment_id", payment_id).execute().data or []

    if allocations:
        # Legacy payment (created before the flat-balance change) — reverse
        # via its recorded invoice allocations, exactly as before.
        total_applied = 0.0
        for alloc in allocations:
            inv = supabase.table("invoices").select("total, paid_amount").eq("id", alloc["invoice_id"]).single().execute()
            if inv.data:
                new_paid = round((inv.data["paid_amount"] or 0) - alloc["amount"], 2)
                if new_paid <= 0:
                    new_status = "unpaid"
                elif new_paid < inv.data["total"]:
                    new_status = "partial"
                else:
                    new_status = "paid"
                supabase.table("invoices").update({
                    "paid_amount": new_paid,
                    "status": new_status,
                }).eq("id", alloc["invoice_id"]).execute()
            total_applied = round(total_applied + alloc["amount"], 2)
        supabase.table("payment_allocations").delete().eq("payment_id", payment_id).execute()
    else:
        # Flat-balance payment (current behavior) — nothing to reverse on
        # invoices; the full amount goes straight back onto the balance.
        total_applied = p.get("amount") or 0

    if p.get("customer_id") and total_applied > 0:
        cust = supabase.table("customers").select("balance").eq("id", p["customer_id"]).single().execute()
        if cust.data:
            new_balance = round((cust.data["balance"] or 0) + total_applied, 2)
            supabase.table("customers").update({"balance": new_balance}).eq("id", p["customer_id"]).execute()

    supabase.table("payments").delete().eq("id", payment_id).execute()

    return {"message": "Payment deleted"}
