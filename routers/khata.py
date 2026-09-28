from fastapi import APIRouter, Depends
from schemas.khata import KhataEntryCreate
from middleware.auth_middleware import get_current_user, get_active_store_id
from database import get_supabase

router = APIRouter()

@router.get("/")
async def list_khata(party_type: str = "customer", user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    return supabase.table("khata_entries").select("*").eq("store_id", store_id).eq("party_type", party_type).order("entry_date", desc=True).execute().data

@router.get("/summary/{party_id}")
async def khata_summary(party_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    entries = supabase.table("khata_entries").select("*").eq("store_id", store_id).eq("party_id", party_id).execute().data
    balance = sum(e["amount"] if e["entry_type"] == "debit" else -e["amount"] for e in entries)
    return {"entries": entries, "balance": round(balance, 2)}

@router.post("/")
async def add_khata_entry(body: KhataEntryCreate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    data = body.model_dump()
    data["store_id"] = store_id
    data["party_id"] = str(data["party_id"])
    data["entry_date"] = str(data["entry_date"])
    return supabase.table("khata_entries").insert(data).execute().data[0]
