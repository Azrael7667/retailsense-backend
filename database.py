from functools import lru_cache

from supabase import create_client, Client
from config import get_settings

settings = get_settings()

# Opening a Supabase client costs ~2 s before its first query (connection
# setup); a client that is already connected answers in ~0.1 s. Clients are
# therefore kept and reused, except where a client holds a login session.


def get_supabase(access_token: str = None) -> Client:
    """
    Anon-key client. Pass the caller's access_token (from get_current_user)
    so Postgres sees a real auth.uid() and RLS policies actually apply —
    without it, every RLS-protected query runs as anonymous and either
    silently returns 0 rows or gets rejected outright.

    With a token the client is reused for that token only, so one user's
    client never carries another user's token. Without a token a fresh client
    is returned, because sign-in / sign-up / sign-out store a session on it.
    """
    if access_token:
        return _client_for_token(access_token)
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache(maxsize=256)
def _client_for_token(access_token: str) -> Client:
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client


@lru_cache(maxsize=1)
def get_token_verifier() -> Client:
    """Shared anon client used only for supabase.auth.get_user(token), which keeps no state on the client."""
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache(maxsize=1)
def get_supabase_admin() -> Client:
    """Service role client — bypasses RLS. Use only in server-side ML/reports."""
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
