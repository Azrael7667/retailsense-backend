from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import get_supabase

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
