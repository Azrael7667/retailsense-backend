"""Helper to get store_id for the authenticated user."""
from database import get_supabase_admin

def get_store_id(user_id: str) -> str:
    # Uses the admin/service-role client on purpose: identity is already
    # verified upstream by get_current_user before this runs, and this
    # lookup only returns a store_id to scope the caller's OWN subsequent
    # queries — it never hands raw table data back to the client. Same
    # precedent as get_current_user_with_role's users lookup.
    supabase = get_supabase_admin()
    result = supabase.table("users").select("store_id").eq("id", user_id).single().execute()
    if not result.data:
        raise ValueError("User has no associated store")
    return result.data["store_id"]
