from fastapi import APIRouter, Depends
from schemas.expense import ExpenseCreate
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from datetime import date
from typing import Optional

router = APIRouter()

@router.get("/")
async def list_expenses(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user=Depends(get_current_user)
):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    q = supabase.table("expenses").select("*").eq("store_id", store_id).order("expense_date", desc=True)
    if start_date:
        q = q.gte("expense_date", str(start_date))
    if end_date:
        q = q.lte("expense_date", str(end_date))
    return q.execute().data

@router.post("/")
async def create_expense(body: ExpenseCreate, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    data = body.model_dump()
    data["store_id"] = store_id
    data["expense_date"] = str(data["expense_date"])
    return supabase.table("expenses").insert(data).execute().data[0]

@router.delete("/{expense_id}")
async def delete_expense(expense_id: str, user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    supabase.table("expenses").delete().eq("id", expense_id).eq("store_id", store_id).execute()
    return {"message": "Expense deleted"}
