from fastapi import APIRouter, Depends

from database import get_supabase_admin
from middleware.auth_middleware import get_active_store_id, get_current_user_with_role

router = APIRouter()


@router.get("")
async def list_activity(
    limit: int = 30,
    user: dict = Depends(get_current_user_with_role),
    store_id: str = Depends(get_active_store_id),
):
    """Latest activity of the selected shop. Owners and accountants see everything; anyone else sees only their own actions."""
    limit = max(1, min(int(limit), 100))
    sb = get_supabase_admin()
    member = sb.table("store_members").select("role").eq("user_id", user["id"]).eq("store_id", store_id).limit(1).execute().data
    role = member[0]["role"] if member else None
    q = sb.table("activity_feed").select("id, user_id, user_name, action, entity, label, summary, created_at").eq("store_id", store_id)
    if role not in ("owner", "accountant"):
        q = q.eq("user_id", user["id"])
    rows = q.order("created_at", desc=True).limit(limit).execute().data or []
    return {"items": [{**r, "mine": r["user_id"] == user["id"]} for r in rows]}
