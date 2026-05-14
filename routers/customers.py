from fastapi import APIRouter, Depends, HTTPException
from schemas.customer import CustomerCreate, CustomerUpdate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from typing import Optional

router = APIRouter()

@router.get("/")
async def list_customers(search: Optional[str] = None, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    q = supabase.table("customers").select("*").eq("store_id", store_id)
    if search:
        q = q.ilike("name", f"%{search}%")
    return q.execute().data

@router.post("/")
async def create_customer(body: CustomerCreate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    data = body.model_dump()
    data["store_id"] = store_id
    return supabase.table("customers").insert(data).execute().data[0]

@router.get("/{customer_id}")
async def get_customer(customer_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    result = supabase.table("customers").select("*").eq("id", customer_id).eq("store_id", store_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Customer not found")
    return result.data

@router.put("/{customer_id}")
async def update_customer(customer_id: str, body: CustomerUpdate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    return supabase.table("customers").update(data).eq("id", customer_id).eq("store_id", store_id).execute().data[0]

@router.delete("/{customer_id}")
async def delete_customer(customer_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    supabase.table("customers").delete().eq("id", customer_id).eq("store_id", store_id).execute()
    return {"message": "Customer deleted"}
