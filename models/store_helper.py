"""Helper to get store_id for the authenticated user."""
from fastapi import HTTPException
from database import get_supabase_admin

def get_store_id(user_id: str, requested_store_id: str | None = None) -> str:
    """
    Resolves which store a request should operate on.

    Called with just user_id (the shape every existing router uses today):
    behaves EXACTLY as before — looks up users.store_id for that user.
    No router needs to change for this to keep working.

    Called with requested_store_id too (new, not yet used by any router):
    verifies, via store_members, that this user actually belongs to that
    store before trusting it. Raises 403 if they don't. This is what the
    upcoming X-Store-Id header will feed into once routers are migrated
    one at a time to pass it through — not done yet, this just adds the
    capability without touching any caller.
    """
    supabase = get_supabase_admin()

    if requested_store_id is not None:
        membership = supabase.table("store_members") \
            .select("store_id") \
            .eq("user_id", user_id) \
            .eq("store_id", requested_store_id) \
            .limit(1).execute()
        if not membership.data:
            raise HTTPException(status_code=403, detail="You don't have access to this store")
        return requested_store_id

    # Original behavior, unchanged — every existing router keeps working.
    result = supabase.table("users").select("store_id").eq("id", user_id).single().execute()
    if not result.data:
        raise ValueError("User has no associated store")
    return result.data["store_id"]


def get_user_stores(user_id: str) -> list[dict]:
    """
    Returns every store this user belongs to (via store_members), each with
    their role in that store and whether it's their default — the data the
    store-switcher dropdown and a future GET /api/auth/my-stores endpoint
    will need. Not called by anything yet; added now so it's ready when
    those pieces are built.
    """
    supabase = get_supabase_admin()
    result = supabase.table("store_members") \
        .select("store_id, role, is_default, stores(name, store_type)") \
        .eq("user_id", user_id) \
        .order("is_default", desc=True) \
        .execute()
    return result.data or []
