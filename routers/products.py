from fastapi import APIRouter, Depends, HTTPException
from schemas.product import ProductCreate, ProductUpdate, ProductOut
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from typing import List, Optional

router = APIRouter()

@router.get("/", response_model=List[dict])
async def list_products(
    category_id: Optional[str] = None,
    search: Optional[str] = None,
    low_stock: bool = False,
    user=Depends(get_current_user)
):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    q = supabase.table("products").select("*, categories(name)").eq("store_id", store_id).eq("is_active", True)
    if category_id:
        q = q.eq("category_id", category_id)
    if search:
        q = q.ilike("name", f"%{search}%")
    if low_stock:
        q = q.lt("stock_quantity", "reorder_level")
    result = q.execute()
    return result.data

@router.post("/", response_model=dict)
async def create_product(body: ProductCreate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    data = body.model_dump()
    data["store_id"] = store_id
    if data.get("category_id"):
        data["category_id"] = str(data["category_id"])
    result = supabase.table("products").insert(data).execute()
    return result.data[0]

@router.get("/{product_id}", response_model=dict)
async def get_product(product_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    result = supabase.table("products").select("*, categories(name)").eq("id", product_id).eq("store_id", store_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Product not found")
    return result.data

@router.put("/{product_id}", response_model=dict)
async def update_product(product_id: str, body: ProductUpdate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    result = supabase.table("products").update(data).eq("id", product_id).eq("store_id", store_id).execute()
    return result.data[0]

@router.delete("/{product_id}")
async def delete_product(product_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    supabase.table("products").update({"is_active": False}).eq("id", product_id).eq("store_id", store_id).execute()
    return {"message": "Product deactivated"}
