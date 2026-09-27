from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import get_supabase, get_supabase_admin

security = HTTPBearer()


class AuthedUser:
    """
    Wraps the Supabase auth user object and also carries the raw JWT, so
    routers can forward it into get_supabase(user.access_token) for
    RLS-aware queries. Attribute access (user.id, user.email, etc.)
    transparently proxies to the wrapped object, so existing code that
    reads those fields keeps working unchanged.
    """
    def __init__(self, supabase_user, access_token: str):
        object.__setattr__(self, "_supabase_user", supabase_user)
        object.__setattr__(self, "access_token", access_token)

    def __getattr__(self, name):
        return getattr(self._supabase_user, name)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Existing dependency — verifies the token and returns the raw Supabase
    auth user object (id, email, etc.). Left unchanged so every endpoint
    already using this keeps working exactly as before.
    """
    token = credentials.credentials
    supabase = get_supabase()
    try:
        response = supabase.auth.get_user(token)
        if not response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )
        return AuthedUser(response.user, token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )


async def get_current_user_with_role(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Like get_current_user, but also resolves store_id + role from our
    own `users` table. Needed for anything that has to know WHO is
    calling in a business sense (invite-staff, role-gated actions) —
    RLS can't help here because these endpoints run with the service
    role and must check the caller's identity themselves.

    Returns: { id, email, full_name, store_id, role, is_active }
    """
    token = credentials.credentials
    supabase = get_supabase()
    try:
        response = supabase.auth.get_user(token)
        if not response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )
        auth_user = response.user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    admin_client = get_supabase_admin()
    row = admin_client.table("users") \
        .select("id, email, full_name, store_id, role, is_active") \
        .eq("id", auth_user.id) \
        .single().execute()

    if not row.data:
        raise HTTPException(status_code=401, detail="No matching user profile found")
    if not row.data.get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been deactivated")

    return row.data


def require_role(*allowed_roles: str):
    """
    Dependency factory for role-gated endpoints.
    Usage: async def x(user=Depends(require_role("owner"))): ...
    """
    async def checker(user: dict = Depends(get_current_user_with_role)):
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(allowed_roles)}",
            )
        return user
    return checker
