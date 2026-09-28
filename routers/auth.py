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
    supabase = get_supabase()
    try:
        res = supabase.auth.sign_up({"email": body.email, "password": body.password})
        user = res.user
        if not user:
            raise HTTPException(status_code=400, detail="Registration failed")

        store = supabase.table("stores").insert({
            "name": body.store_name,
            "store_type": body.store_type,
            "owner_name": body.full_name,
        }).execute()
        store_id = store.data[0]["id"]

        supabase.table("users").insert({
            "id": user.id,
            "store_id": store_id,
            "full_name": body.full_name,
            "email": body.email,
            "role": "owner",
            "permissions": {k: True for k in DEFAULT_PERMISSIONS},
        }).execute()

        supabase.table("store_members").insert({
            "user_id": user.id,
            "store_id": store_id,
            "role": "owner",
            "is_default": True,
        }).execute()

        _seed_categories(store_id, body.store_type)

        return {"message": "Account created successfully", "store_id": store_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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


VALID_INVITE_ROLES = {"accountant", "auditor", "staff"}

@router.post("/invite-staff")
async def invite_staff(body: InviteStaffRequest, current_user=Depends(require_role("owner"))):
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
async def list_staff(current_user=Depends(require_role("owner"))):
    supabase = get_supabase_admin()
    res = supabase.table("users") \
        .select("id, full_name, email, role, phone, is_active, created_at, permissions") \
        .eq("store_id", current_user["store_id"]) \
        .order("created_at").execute()
    staff = []
    for row in res.data:
        row = dict(row)
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
    current_user=Depends(require_role("owner")),
):
    supabase = get_supabase_admin()
    target = supabase.table("users").select("id, store_id, role, permissions") \
        .eq("id", staff_id).single().execute()
    if not target.data or target.data["store_id"] != current_user["store_id"]:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if target.data["role"] == "owner":
        raise HTTPException(status_code=400, detail="The owner always has full access — nothing to toggle")

    unknown_keys = set(body.permissions) - set(DEFAULT_PERMISSIONS)
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"Unknown permission(s): {sorted(unknown_keys)}")

    current_permissions = {**DEFAULT_PERMISSIONS, **(target.data.get("permissions") or {})}
    merged = {**current_permissions, **body.permissions}

    updated = supabase.table("users").update({"permissions": merged}).eq("id", staff_id).execute().data[0]
    return updated


@router.patch("/staff/{staff_id}/deactivate")
async def deactivate_staff(staff_id: str, current_user=Depends(require_role("owner"))):
    if staff_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")

    supabase = get_supabase_admin()
    target = supabase.table("users").select("id, store_id").eq("id", staff_id).single().execute()
    if not target.data or target.data["store_id"] != current_user["store_id"]:
        raise HTTPException(status_code=404, detail="Staff member not found")

    supabase.table("users").update({"is_active": False}).eq("id", staff_id).execute()
    return {"message": "Staff member deactivated"}


@router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: str, current_user=Depends(require_role("owner"))):
    if staff_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    supabase = get_supabase_admin()
    target = supabase.table("users").select("id, store_id, role").eq("id", staff_id).single().execute()
    if not target.data or target.data["store_id"] != current_user["store_id"]:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if target.data["role"] == "owner":
        raise HTTPException(status_code=400, detail="Cannot delete the store owner")

    try:
        supabase.table("users").delete().eq("id", staff_id).execute()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not delete staff profile (they may have linked records — try deactivating instead): {e}")

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
    supabase = get_supabase()
    names = CATEGORY_PRESETS.get(store_type, CATEGORY_PRESETS["general"])
    rows = [{"store_id": store_id, "name": n, "is_system": True} for n in names]
    supabase.table("categories").insert(rows).execute()
