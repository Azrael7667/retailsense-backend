"""Helper to get store_id for the authenticated user."""
from database import get_supabase

def get_store_id(user_id: str) -> str:
    supabase = get_supabase()
    result = supabase.table("users").select("store_id").eq("id", user_id).single().execute()
    if not result.data:
        raise ValueError("User has no associated store")
    return result.data["store_id"]
