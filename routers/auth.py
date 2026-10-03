from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from database import get_supabase, get_supabase_admin
from middleware.auth_middleware import require_role, get_current_user, DEFAULT_PERMISSIONS
from models.store_helper import get_user_stores
from config import get_settings

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    store_name: str
    store_type: str

class InviteStaffRequest(BaseModel):
    email: str
    full_name: str
    role: str
    phone: str | None = None

class CreateStoreRequest(BaseModel):
    store_name: str
    store_type: str
    address: str | None = None
    phone: str | None = None
    vat_number: str | None = None

class UpdatePermissionsRequest(BaseModel):
    permissions: dict[str, bool]


def default_permissions_for_role(role: str) -> dict:
    """
    Starting permissions for a newly invited staff member, before the owner
    customizes anything. Mirrors the optional SQL backfill that re-applied
    these for pre-existing accounts — kept in sync manually since that SQL
    only ran once, on request.

    Accountant gets everything (needs full financial + operational access).
    Auditor gets every *_view permission plus all reports_* (reviews, never
    creates/edits/deletes). Staff gets nothing until the owner grants it.
    """
    if role == "accountant":
        return {k: True for k in DEFAULT_PERMISSIONS}
    if role == "auditor":
        return {
            k: (True if (k.endswith("_view") or k.startswith("reports_")) else False)
            for k in DEFAULT_PERMISSIONS
        }
    return dict(DEFAULT_PERMISSIONS)  # staff — everything off


@router.post("/login")
async def login(body: LoginRequest):
    supabase = get_supabase()
    try:
        res = supabase.auth.sign_in_with_password({"email": body.email, "password": body.password})
        return {"access_token": res.session.access_token, "user": res.user}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/register")
async def register(body: RegisterRequest):
    """
    Public signup. Runs entirely with the service-role client so it does not
    depend on a session existing (email confirmation) or on the stores RLS
    policies. The auth user is created already-confirmed so the frontend can
    sign in straight away. If any step fails, everything created so far is
    rolled back so no orphaned auth users or stores are left behind.
    """
    email = body.email.strip().lower()
    full_name = body.full_name.strip()
    store_name = body.store_name.strip()
    store_type = (body.store_type or "general").strip().lower()

    if not email or not full_name or not store_name:
        raise HTTPException(status_code=400, detail="Name, store name and email are required")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if store_type not in CATEGORY_PRESETS:
        store_type = "general"

    supabase = get_supabase_admin()
    user_id = None
    store_id = None

    # 1. Auth user (already confirmed)
    try:
        created = supabase.auth.admin.create_user({
            "email": email,
            "password": body.password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name},
        })
        if not created.user:
            raise HTTPException(status_code=400, detail="Registration failed")
        user_id = created.user.id
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "already" in msg.lower() or "registered" in msg.lower():
            raise HTTPException(status_code=400, detail="An account with this email already exists")
        raise HTTPException(status_code=400, detail=f"Could not create account: {msg}")

    # 2. Store, profile, membership, categories — roll back everything on failure
    try:
        store = supabase.table("stores").insert({
            "name": store_name,
            "store_type": store_type,
            "owner_name": full_name,
        }).execute()
        store_id = store.data[0]["id"]

        supabase.table("users").insert({
            "id": user_id,
            "store_id": store_id,
            "full_name": full_name,
            "email": email,
            "role": "owner",
            "permissions": {k: True for k in DEFAULT_PERMISSIONS},
        }).execute()

        supabase.table("store_members").insert({
            "user_id": user_id,
            "store_id": store_id,
            "role": "owner",
            "is_default": True,
        }).execute()

        _seed_categories(store_id, store_type)

        return {"message": "Account created successfully", "store_id": store_id}
    except Exception as e:
        # Best-effort cleanup, child rows first
        for cleanup in (
            lambda: supabase.table("categories").delete().eq("store_id", store_id).execute() if store_id else None,
            lambda: supabase.table("store_members").delete().eq("user_id", user_id).execute(),
            lambda: supabase.table("users").delete().eq("id", user_id).execute(),
            lambda: supabase.table("stores").delete().eq("id", store_id).execute() if store_id else None,
            lambda: supabase.auth.admin.delete_user(user_id),
        ):
            try:
                cleanup()
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=f"Registration failed: {e}")

@router.post("/logout")
async def logout():
    supabase = get_supabase()
    supabase.auth.sign_out()
    return {"message": "Logged out"}


@router.get("/my-stores")
async def my_stores(current_user=Depends(get_current_user)):
    stores = get_user_stores(current_user.id)
    return {"stores": stores}


@router.post("/create-store")
async def create_store(body: CreateStoreRequest, current_user=Depends(get_current_user)):
    supabase = get_supabase_admin()

    existing = supabase.table("users").select("full_name").eq("id", current_user.id).limit(1).execute()
    owner_name = existing.data[0]["full_name"] if existing.data else None

    store = supabase.table("stores").insert({
        "name": body.store_name,
        "store_type": body.store_type,
        "owner_name": owner_name,
        "address": body.address,
        "phone": body.phone,
        "vat_number": body.vat_number,
    }).execute()
    store_id = store.data[0]["id"]

    supabase.table("store_members").insert({
        "user_id": current_user.id,
        "store_id": store_id,
        "role": "owner",
        "is_default": False,
    }).execute()

    _seed_categories(store_id, body.store_type)

    return {"message": "Store created", "store_id": store_id}


from middleware.auth_middleware import get_active_store_id, get_current_user_with_role

VALID_INVITE_ROLES = {"accountant", "auditor", "staff"}

async def owner_of_active_shop(
    user: dict = Depends(get_current_user_with_role),
    store_id: str = Depends(get_active_store_id),
):
    """Only the owner of the SELECTED shop may manage its staff. The returned user carries that shop as store_id."""
    supabase = get_supabase_admin()
    rows = supabase.table("store_members").select("role").eq("user_id", user["id"]).eq("store_id", store_id).limit(1).execute().data
    if not rows or rows[0]["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the owner of this shop can manage its staff")
    return {**user, "store_id": store_id}


def _membership(supabase, user_id: str, store_id: str):
    rows = supabase.table("store_members").select("role").eq("user_id", user_id).eq("store_id", store_id).limit(1).execute().data
    return rows[0] if rows else None


@router.post("/invite-staff")
async def invite_staff(body: InviteStaffRequest, current_user=Depends(owner_of_active_shop)):
    if body.role not in VALID_INVITE_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {sorted(VALID_INVITE_ROLES)}")

    supabase = get_supabase_admin()
    settings = get_settings()

    frontend_base = settings.allowed_origins.split(",")[0].strip()
    redirect_to = f"{frontend_base}/accept-invite"

    try:
        created = supabase.auth.admin.invite_user_by_email(
            body.email,
            {"redirect_to": redirect_to},
        )
        new_user = created.user
        if not new_user:
            raise HTTPException(status_code=400, detail="Could not send invite")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not send invite: {e}")

    try:
        supabase.table("users").insert({
            "id":         new_user.id,
            "store_id":   current_user["store_id"],
            "full_name":  body.full_name,
            "email":      body.email,
            "role":       body.role,
            "phone":      body.phone,
            "is_active":  True,
            "invited_by": current_user["id"],
            "permissions": default_permissions_for_role(body.role),
        }).execute()

        supabase.table("store_members").insert({
            "user_id":    new_user.id,
            "store_id":   current_user["store_id"],
            "role":       body.role,
            "is_default": True,
        }).execute()
    except Exception as e:
        supabase.auth.admin.delete_user(new_user.id)
        raise HTTPException(status_code=400, detail=f"Could not save staff profile: {e}")

    return {
        "message": f"Invite sent to {body.email}",
        "user_id": new_user.id,
    }


@router.get("/staff")
async def list_staff(current_user=Depends(owner_of_active_shop)):
    supabase = get_supabase_admin()
    members = supabase.table("store_members").select("user_id, role").eq("store_id", current_user["store_id"]).execute().data or []
    role_here = {m["user_id"]: m["role"] for m in members}
    if not role_here:
        return {"staff": []}
    res = supabase.table("users") \
        .select("id, full_name, email, role, phone, is_active, created_at, permissions") \
        .in_("id", list(role_here)) \
        .order("created_at").execute()
    staff = []
    for row in res.data:
        row = dict(row)
        row["role"] = role_here.get(row["id"], row["role"])          # the role in THIS shop
        if row["role"] == "owner":
            row["permissions"] = {k: True for k in DEFAULT_PERMISSIONS}
        else:
            row["permissions"] = {**DEFAULT_PERMISSIONS, **(row.get("permissions") or {})}
        staff.append(row)
    return {"staff": staff}


@router.patch("/staff/{staff_id}/permissions")
async def update_staff_permissions(
    staff_id: str,
    body: UpdatePermissionsRequest,
    current_user=Depends(owner_of_active_shop),
):
    supabase = get_supabase_admin()
    member = _membership(supabase, staff_id, current_user["store_id"])
    target = supabase.table("users").select("id, role, permissions").eq("id", staff_id).single().execute()
    if not member or not target.data:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if member["role"] == "owner":
        raise HTTPException(status_code=400, detail="The owner always has full access, nothing to toggle")

    unknown_keys = set(body.permissions) - set(DEFAULT_PERMISSIONS)
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"Unknown permission(s): {sorted(unknown_keys)}")

    current_permissions = {**DEFAULT_PERMISSIONS, **(target.data.get("permissions") or {})}
    merged = {**current_permissions, **body.permissions}

    updated = supabase.table("users").update({"permissions": merged}).eq("id", staff_id).execute().data[0]
    return updated


@router.patch("/staff/{staff_id}/deactivate")
async def deactivate_staff(staff_id: str, current_user=Depends(owner_of_active_shop)):
    if staff_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")

    supabase = get_supabase_admin()
    member = _membership(supabase, staff_id, current_user["store_id"])
    if not member:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if member["role"] == "owner":
        raise HTTPException(status_code=400, detail="Cannot deactivate the store owner")

    supabase.table("users").update({"is_active": False}).eq("id", staff_id).execute()
    return {"message": "Staff member deactivated"}


@router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: str, current_user=Depends(owner_of_active_shop)):
    if staff_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    supabase = get_supabase_admin()
    shop = current_user["store_id"]
    memberships = supabase.table("store_members").select("store_id, role").eq("user_id", staff_id).execute().data or []
    here = next((m for m in memberships if m["store_id"] == shop), None)
    if not here:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if here["role"] == "owner":
        raise HTTPException(status_code=400, detail="Cannot delete the store owner")

    others = [m for m in memberships if m["store_id"] != shop]
    if others:
        # they also work in another shop: only remove them from this one and keep their account
        supabase.table("store_members").delete().eq("user_id", staff_id).eq("store_id", shop).execute()
        home = supabase.table("users").select("store_id").eq("id", staff_id).single().execute().data
        if home and home["store_id"] == shop:
            supabase.table("users").update({"store_id": others[0]["store_id"]}).eq("id", staff_id).execute()
        return {"message": "Staff member removed from this shop"}

    # their only shop: remove the whole account, as before
    supabase.table("store_members").delete().eq("user_id", staff_id).eq("store_id", shop).execute()
    try:
        supabase.table("users").delete().eq("id", staff_id).execute()
    except Exception as e:
        supabase.table("store_members").insert({"user_id": staff_id, "store_id": shop, "role": here["role"], "is_default": True}).execute()
        raise HTTPException(status_code=400, detail=f"Could not delete staff profile (they may have linked records, try deactivating instead): {e}")

    try:
        supabase.auth.admin.delete_user(staff_id)
    except Exception as e:
        return {"message": "Staff profile deleted, but auth account cleanup failed", "warning": str(e)}

    return {"message": "Staff member deleted"}

CATEGORY_PRESETS = {
    "grocery":     ["Rice & Flour","Pulses & Lentils","Spices","Oil & Ghee","Snacks","Beverages","Dairy","Personal Care","Household","Others"],
    "clothing":    ["Men's Wear","Women's Wear","Kids Wear","Footwear","Accessories","Ethnic Wear","Innerwear","Others"],
    "electronics": ["Mobile Phones","Accessories","Laptops","TVs & Monitors","Audio","Kitchen Appliances","Batteries","Others"],
    "pharmacy":    ["Prescription Medicines","OTC Medicines","Vitamins & Supplements","Personal Care","Baby Care","Medical Devices","Others"],
    "general":     ["Category 1","Category 2","Category 3","Category 4","Others"],
}

def _seed_categories(store_id: str, store_type: str):
    # Admin client: this runs during signup, before the user has a session
    supabase = get_supabase_admin()
    names = CATEGORY_PRESETS.get(store_type, CATEGORY_PRESETS["general"])
    rows = [{"store_id": store_id, "name": n, "is_system": True} for n in names]
    supabase.table("categories").insert(rows).execute()
