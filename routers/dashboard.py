from fastapi import APIRouter, Depends
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id
from database import get_supabase
from datetime import date, timedelta

router = APIRouter()

@router.get("/summary")
async def dashboard_summary(user=Depends(get_current_user)):
    supabase = get_supabase()
    store_id = get_store_id(user.id)
    today = date.today()
    month_start = today.replace(day=1)

    invoices_month = supabase.table("invoices").select("total").eq("store_id", store_id).gte("invoice_date", str(month_start)).lte("invoice_date", str(today)).execute().data
    total_customers = supabase.table("customers").select("id", count="exact").eq("store_id", store_id).execute()
    low_stock = supabase.table("products").select("id", count="exact").eq("store_id", store_id).eq("is_active", True).execute()
    expenses_month = supabase.table("expenses").select("amount").eq("store_id", store_id).gte("expense_date", str(month_start)).execute().data

    monthly_revenue = round(sum(i["total"] for i in invoices_month), 2)
    monthly_expenses = round(sum(e["amount"] for e in expenses_month), 2)

    return {
        "monthly_revenue":  monthly_revenue,
        "monthly_expenses": monthly_expenses,
        "net_profit":       round(monthly_revenue - monthly_expenses, 2),
        "total_customers":  total_customers.count or 0,
        "total_invoices":   len(invoices_month),
    }
