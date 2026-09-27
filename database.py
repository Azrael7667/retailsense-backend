from supabase import create_client, Client
from config import get_settings

settings = get_settings()

def get_supabase(access_token: str = None) -> Client:
    """
    Anon-key client. Pass the caller's access_token (from get_current_user)
    so Postgres sees a real auth.uid() and RLS policies actually apply —
    without it, every RLS-protected query runs as anonymous and either
    silently returns 0 rows or gets rejected outright.
    """
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    if access_token:
        client.postgrest.auth(access_token)
    return client

def get_supabase_admin() -> Client:
    """Service role client — bypasses RLS. Use only in server-side ML/reports."""
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
