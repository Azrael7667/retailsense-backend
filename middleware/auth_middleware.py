from fastapi import Depends, Header, HTTPException, status
import base64
import json
import time
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import get_supabase, get_supabase_admin
from models.store_helper import get_store_id

security = HTTPBearer()


MAX_SESSION_SECONDS = 24 * 60 * 60     # ask the user to log in again after 24 hours


def _login_time(token: str):
    """When this login started, read from the token's amr claim (earliest entry). None if the token has no usable entry."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(part))
        stamps = [int(a["timestamp"]) for a in claims.get("amr", []) if isinstance(a, dict) and a.get("timestamp")]
        return min(stamps) if stamps else None
    except Exception:
        return None


def _enforce_session_age(token: str):
    started = _login_time(token)
    if started and time.time() - started > MAX_SESSION_SECONDS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired, please log in again")


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
    _enforce_session_age(token)
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


async def get_active_store_id(
    x_store_id: str | None = Header(default=None, alias="X-Store-Id"),
    user: AuthedUser = Depends(get_current_user),
) -> str:
    """
    Resolves which store the current request applies to, from the
    X-Store-Id header the frontend sends on every call (see apiClient.js's
    request interceptor, which attaches it from storeStore).

    Verifies membership via store_members before trusting it — even if
    someone forged the header value, get_store_id() checks it against
    store_members and raises 403 if they don't actually belong to that
    store. See store_helper.py.

    Falls back to the user's original users.store_id (get_store_id's
    original one-argument behavior) if no header is sent at all — keeps
    any older client or direct API call working exactly as before.

    This is what routers depend on going forward, in place of manually
    calling get_store_id(user.id) themselves:
        store_id: str = Depends(get_active_store_id)
    """
    if x_store_id:
        return get_store_id(user.id, x_store_id)
    return get_store_id(user.id)


DEFAULT_PERMISSIONS = {
    # Sales (Sales, Quotation, Payment In, Sales Return, Other Income)
    "sales_create": False, "sales_edit": False, "sales_delete": False, "sales_view": False,
    # Purchase (Purchase, Payment Out, Purchase Return)
    "purchase_create": False, "purchase_edit": False, "purchase_delete": False, "purchase_view": False,
    # Income & Expenses
    "income_expense_create": False, "income_expense_edit": False, "income_expense_delete": False, "income_expense_view": False,
    # Inventory — Item
    "item_create": False, "item_edit": False, "item_delete": False, "item_view": False,
    # Inventory — Item Category
    "item_category_edit": False, "item_category_delete": False, "item_category_view": False,
    # Stock Adjustments
    "adjustments_create": False, "adjustments_edit": False, "adjustments_delete": False,
    # Customers
    "customer_create": False, "customer_edit": False, "customer_delete": False, "customer_view": False,
    # Suppliers
    "supplier_create": False, "supplier_edit": False, "supplier_delete": False,
    # Manage Staffs
    "staff_create": False, "staff_edit": False, "staff_delete": False, "staff_view": False,
    # Reports
    "reports_business_status": False, "reports_homepage_stats": False, "reports_income_expense": False,
    "reports_item": False, "reports_party": False, "reports_transaction": False,
    # Other Permissions
    "manage_party_adjustments": False, "hide_purchase_price": False, "lock_transaction_date": False,
}


async def get_current_user_with_role(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Like get_current_user, but also resolves store_id + role + permissions
    from our own `users` table. Needed for anything that has to know WHO is
    calling in a business sense (invite-staff, role-gated actions, and now
    permission-gated actions) — RLS can't help here because these endpoints
    run with the service role and must check the caller's identity
    themselves.

    Returns: { id, email, full_name, store_id, role, is_active, permissions }
    permissions is always a full dict (missing keys fall back to False via
    DEFAULT_PERMISSIONS) so callers never need to guard against a partial
    or missing permissions object — except owner, who is ALWAYS granted
    every permission regardless of what's stored, since an owner locking
    themselves out is never the intended behavior of a toggle meant for
    staff.
    """
    token = credentials.credentials
    _enforce_session_age(token)
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
        .select("id, email, full_name, store_id, role, is_active, permissions") \
        .eq("id", auth_user.id) \
        .single().execute()

    if not row.data:
        raise HTTPException(status_code=401, detail="No matching user profile found")
    if not row.data.get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been deactivated")

    data = dict(row.data)
    if data["role"] == "owner":
        data["permissions"] = {k: True for k in DEFAULT_PERMISSIONS}
    else:
        data["permissions"] = {**DEFAULT_PERMISSIONS, **(data.get("permissions") or {})}

    return data


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


def require_permission(permission: str):
    """
    Dependency factory for permission-gated actions. Owner always passes,
    per get_current_user_with_role's override above.
    Usage: async def x(user=Depends(require_permission("sales_delete"))): ...
    """
    async def checker(user: dict = Depends(get_current_user_with_role)):
        if not user["permissions"].get(permission, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to do this. Ask your store owner to grant it.",
            )
        return user
    return checker
