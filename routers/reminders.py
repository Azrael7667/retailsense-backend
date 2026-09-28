from fastapi import APIRouter, Depends, HTTPException
from schemas.reminder import ReminderCreate
from middleware.auth_middleware import get_current_user, get_active_store_id
from database import get_supabase
from typing import Optional

router = APIRouter()

@router.get("/")
async def list_reminders(
    party_id: Optional[str] = None,
    status: Optional[str] = None,
    user=Depends(get_current_user),
    store_id: str = Depends(get_active_store_id)
):
    supabase = get_supabase(user.access_token)
    q = supabase.table("reminders").select("*").eq("store_id", store_id).order("remind_date")
    if party_id:
        q = q.eq("party_id", party_id)
    if status:
        q = q.eq("status", status)
    return q.execute().data

@router.post("/")
async def create_reminder(body: ReminderCreate, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    data = {
        "store_id": store_id,
        "party_type": body.party_type,
        "party_id": str(body.party_id),
        "remind_date": str(body.remind_date),
        "note": body.note,
        "status": "pending",
    }
    reminder = supabase.table("reminders").insert(data).execute().data[0]
    return reminder

@router.patch("/{reminder_id}/done")
async def mark_reminder_done(reminder_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    existing = supabase.table("reminders").select("id").eq("id", reminder_id).eq("store_id", store_id).single().execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Reminder not found")
    updated = supabase.table("reminders").update({"status": "done"}).eq("id", reminder_id).execute().data[0]
    return updated

@router.delete("/{reminder_id}")
async def delete_reminder(reminder_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    supabase = get_supabase(user.access_token)
    existing = supabase.table("reminders").select("id").eq("id", reminder_id).eq("store_id", store_id).single().execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Reminder not found")
    supabase.table("reminders").delete().eq("id", reminder_id).execute()
    return {"message": "Reminder deleted"}
