from fastapi import APIRouter, Depends
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None

@router.get("/")
async def list_categories(user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    result = supabase.table("categories").select("*").eq("store_id", store_id).execute()
    return result.data

@router.post("/")
async def create_category(body: CategoryCreate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    result = supabase.table("categories").insert({"store_id": store_id, **body.model_dump()}).execute()
    return result.data[0]

@router.delete("/{category_id}")
async def delete_category(category_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    supabase.table("categories").delete().eq("id", category_id).eq("store_id", store_id).eq("is_system", False).execute()
    return {"message": "Category deleted"}
