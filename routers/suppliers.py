from fastapi import APIRouter, Depends, HTTPException
from schemas.supplier import SupplierCreate, SupplierUpdate
from middleware.auth_middleware import get_current_user, get_active_store_id
from database import get_supabase
from typing import Optional

router = APIRouter()

@router.get("/")
async def list_suppliers(search: Optional[str] = None, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    q = supabase.table("suppliers").select("*").eq("store_id", store_id)
    if search:
        q = q.ilike("name", f"%{search}%")
    return q.execute().data

@router.post("/")
async def create_supplier(body: SupplierCreate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    data = body.model_dump()
    data["store_id"] = store_id
    return supabase.table("suppliers").insert(data).execute().data[0]

@router.get("/{supplier_id}")
async def get_supplier(supplier_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    result = supabase.table("suppliers").select("*").eq("id", supplier_id).eq("store_id", store_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return result.data

@router.put("/{supplier_id}")
async def update_supplier(supplier_id: str, body: SupplierUpdate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    return supabase.table("suppliers").update(data).eq("id", supplier_id).eq("store_id", store_id).execute().data[0]

@router.delete("/{supplier_id}")
async def delete_supplier(supplier_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    supabase.table("suppliers").delete().eq("id", supplier_id).eq("store_id", store_id).execute()
    return {"message": "Supplier deleted"}
