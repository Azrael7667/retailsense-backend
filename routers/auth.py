from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from database import get_supabase, get_supabase_admin
from middleware.auth_middleware import require_role
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
    store_type: str   # grocery | clothing | electronics | pharmacy | general

class InviteStaffRequest(BaseModel):
    email: str
    full_name: str
    role: str            # accountant | auditor | staff  (never "owner" via invite)
    phone: str | None = None

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
        # 1. Create auth user
        res = supabase.auth.sign_up({"email": body.email, "password": body.password})
        user = res.user
        if not user:
            raise HTTPException(status_code=400, detail="Registration failed")

        # 2. Create store
        store = supabase.table("stores").insert({
            "name": body.store_name,
            "store_type": body.store_type,
            "owner_name": body.full_name,
        }).execute()
        store_id = store.data[0]["id"]

        # 3. Create user profile
        supabase.table("users").insert({
            "id": user.id,
            "store_id": store_id,
            "full_name": body.full_name,
            "email": body.email,
            "role": "owner",
        }).execute()

        # 4. Seed default categories based on store type
        _seed_categories(store_id, body.store_type)

        return {"message": "Account created successfully", "store_id": store_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/logout")
async def logout():
    supabase = get_supabase()
    supabase.auth.sign_out()
    return {"message": "Logged out"}


VALID_INVITE_ROLES = {"accountant", "auditor", "staff"}

@router.post("/invite-staff")
async def invite_staff(body: InviteStaffRequest, current_user=Depends(require_role("owner"))):
    """
    Owner-only. Sends a real Supabase invite email (magic link) to the
    staff member and creates their `users` row in the SAME store as the
    calling owner. They click the link, land on /accept-invite in the
    frontend, and set their own password there — no temp password ever
    exists or needs to be relayed manually.

    NOTE: uses Supabase's built-in email service (no custom SMTP
    configured yet) — this is rate-limited to a handful of emails/hour,
    fine for now but worth moving to real SMTP before onboarding many
    staff at once.
    """
    if body.role not in VALID_INVITE_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {sorted(VALID_INVITE_ROLES)}")

    supabase = get_supabase_admin()
    settings = get_settings()

    # First allowed origin doubles as our frontend base URL for the
    # redirect target after the user clicks the emailed invite link.
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
        }).execute()
    except Exception as e:
        # Roll back the orphaned auth user if the profile insert fails
        supabase.auth.admin.delete_user(new_user.id)
        raise HTTPException(status_code=400, detail=f"Could not save staff profile: {e}")

    return {
        "message": f"Invite sent to {body.email}",
        "user_id": new_user.id,
    }


@router.get("/staff")
async def list_staff(current_user=Depends(require_role("owner"))):
    """Owner-only. Lists everyone in the owner's store."""
    supabase = get_supabase_admin()
    res = supabase.table("users") \
        .select("id, full_name, email, role, phone, is_active, created_at") \
        .eq("store_id", current_user["store_id"]) \
        .order("created_at").execute()
    return {"staff": res.data}


@router.patch("/staff/{staff_id}/deactivate")
async def deactivate_staff(staff_id: str, current_user=Depends(require_role("owner"))):
    """Owner-only. Deactivates a staff member (soft — doesn't delete their history)."""
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
    """
    Owner-only. Permanently removes a staff member — deletes their
    Supabase Auth account (so they can never log in again, even if
    re-invited later with a fresh flow) and their `users` profile row.

    This is a hard delete, unlike /deactivate. If the staff member has
    activity_log entries or other records referencing their user id,
    those references will either cascade or block deletion depending
    on how the FK is set up — if this errors with a foreign key
    violation, deactivate instead of delete for that person.
    """
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
        # Profile row is already gone at this point; auth cleanup failing
        # isn't fatal but is worth surfacing rather than silently swallowing.
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
