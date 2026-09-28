from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import get_supabase, get_supabase_admin
from middleware.auth_middleware import get_current_user, AuthedUser

router = APIRouter()

class AdminRegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str


async def get_current_admin(user: AuthedUser = Depends(get_current_user)):
    """
    Verifies the caller's Supabase token belongs to someone with a
    platform_admins row — completely separate from store membership.
    A person can be both a store owner AND a platform admin (two
    different rows, same login), or neither, or just one.
    """
    supabase_admin = get_supabase_admin()
    row = supabase_admin.table("platform_admins").select("id, email, full_name").eq("id", user.id).single().execute()
    if not row.data:
        raise HTTPException(status_code=403, detail="Not a platform admin")
    return row.data


@router.post("/bootstrap")
async def bootstrap_admin(body: AdminRegisterRequest):
    """
    Creates the FIRST platform admin — open (no auth required), but only
    works while platform_admins is empty. Once one exists, this always
    403s and /invite (admin-only) is the only way to add more.
    """
    supabase_admin = get_supabase_admin()
    existing = supabase_admin.table("platform_admins").select("id", count="exact").execute()
    if (existing.count or 0) > 0:
        raise HTTPException(status_code=403, detail="A platform admin already exists. Ask them to invite you instead.")

    supabase = get_supabase()
    try:
        res = supabase.auth.sign_up({"email": body.email, "password": body.password})
        new_user = res.user
        if not new_user:
            raise HTTPException(status_code=400, detail="Could not create account")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    supabase_admin.table("platform_admins").insert({
        "id": new_user.id,
        "email": body.email,
        "full_name": body.full_name,
    }).execute()

    return {"message": "Platform admin account created — you can now log in with it."}


@router.post("/invite")
async def invite_admin(body: AdminRegisterRequest, current_admin: dict = Depends(get_current_admin)):
    """Admin-only. Adds another platform admin (same login flow as bootstrap)."""
    supabase = get_supabase()
    try:
        res = supabase.auth.sign_up({"email": body.email, "password": body.password})
        new_user = res.user
        if not new_user:
            raise HTTPException(status_code=400, detail="Could not create account")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    supabase_admin = get_supabase_admin()
    supabase_admin.table("platform_admins").insert({
        "id": new_user.id,
        "email": body.email,
        "full_name": body.full_name,
    }).execute()

    return {"message": f"{body.email} added as a platform admin"}


@router.get("/me")
async def me(current_admin: dict = Depends(get_current_admin)):
    return current_admin


@router.get("/stores")
async def list_stores(current_admin: dict = Depends(get_current_admin)):
    """
    Every store on the platform — owner name/email, contact details,
    staff count, created date. Owner is resolved via store_members
    (role='owner') rather than users.store_id, since a person can now own
    multiple stores and users.store_id only ever reflects their default.
    """
    supabase_admin = get_supabase_admin()
    stores = supabase_admin.table("stores").select("*").order("created_at", desc=True).execute().data or []

    store_ids = [s["id"] for s in stores]
    members = []
    if store_ids:
        members = supabase_admin.table("store_members").select("store_id, user_id, role").in_("store_id", store_ids).execute().data or []

    owner_by_store = {}
    member_count_by_store = {}
    for m in members:
        member_count_by_store[m["store_id"]] = member_count_by_store.get(m["store_id"], 0) + 1
        if m["role"] == "owner" and m["store_id"] not in owner_by_store:
            owner_by_store[m["store_id"]] = m["user_id"]

    owner_ids = list(set(owner_by_store.values()))
    owners = {}
    if owner_ids:
        rows = supabase_admin.table("users").select("id, full_name, email, phone").in_("id", owner_ids).execute().data or []
        owners = {r["id"]: r for r in rows}

    result = []
    for s in stores:
        owner = owners.get(owner_by_store.get(s["id"]), {})
        result.append({
            "id": s["id"],
            "name": s["name"],
            "store_type": s.get("store_type"),
            "address": s.get("address"),
            "phone": s.get("phone"),
            "vat_number": s.get("vat_number"),
            "created_at": s.get("created_at"),
            "owner_name": owner.get("full_name"),
            "owner_email": owner.get("email"),
            "owner_phone": owner.get("phone"),
            "staff_count": member_count_by_store.get(s["id"], 0),
        })
    return {"stores": result}
