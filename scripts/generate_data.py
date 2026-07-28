"""
RetailSense Nepal - Synthetic Data Generator
Bijeta Auto Parts - Commercial Vehicle Parts
Kathmandu, Nepal

Vehicles: Tata Sumo, Tata Ace/Mega, Mahindra Bolero, 
          Mahindra Scorpio, Mahindra Yoddha, Small Commercial

Run: python scripts/generate_data.py
"""

import random
import uuid
from datetime import datetime, date, timedelta
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

# ── Configuration ──────────────────────────────────────────────────────────────
STORE_NAME  = "Bijeta Auto Parts"
START_DATE  = date(2024, 1, 1)
END_DATE    = date(2024, 12, 31)
random.seed(42)

# ── Get store_id ───────────────────────────────────────────────────────────────
def get_store_id():
    result = supabase.table("stores").select("id").eq("name", STORE_NAME).single().execute()
    if not result.data:
        raise ValueError(f"Store '{STORE_NAME}' not found in database.")
    return result.data["id"]

# ── Products ───────────────────────────────────────────────────────────────────
# (name, cost_price, selling_price, unit, category, product_type, avg_daily_sales)
# Prices in NPR — Indian prices × 1.6 approx conversion + Nepal margin
PRODUCTS = [

    # ── Engine Oils ────────────────────────────────────────────────────────────
    ("Tata Genuine Oil CH4 1L",         580,  700, "litre", "Lubricants & Oils", "fast", 4.0),
    ("Tata Genuine Oil CI4 1L",         630,  750, "litre", "Lubricants & Oils", "fast", 3.5),
    ("Castrol CRB Turbo 1L",            520,  650, "litre", "Lubricants & Oils", "fast", 3.0),
    ("Servo Pride 1L",                  480,  600, "litre", "Lubricants & Oils", "fast", 2.5),
    ("Veedol 20W50 1L",                 450,  570, "litre", "Lubricants & Oils", "fast", 2.0),
    ("Gear Oil 90 (1L)",                380,  500, "litre", "Lubricants & Oils", "slow", 1.2),
    ("Coolant 1L",                      280,  380, "litre", "Lubricants & Oils", "fast", 1.5),
    ("Brake Oil DOT3 500ml",             90,  140, "bottle","Lubricants & Oils", "fast", 2.5),
    ("Grease 500g",                     200,  280, "tin",   "Lubricants & Oils", "slow", 0.8),

    # ── Filters ────────────────────────────────────────────────────────────────
    ("Air Filter (Tata Sumo)",          380,  520, "pcs",   "Filters",           "fast", 1.8),
    ("Air Filter (Bolero/Scorpio)",     420,  580, "pcs",   "Filters",           "fast", 1.5),
    ("Air Filter (Tata Ace)",           280,  400, "pcs",   "Filters",           "fast", 1.5),
    ("Oil Filter (Tata Sumo)",          220,  320, "pcs",   "Filters",           "fast", 2.0),
    ("Oil Filter (Bolero)",             240,  340, "pcs",   "Filters",           "fast", 1.8),
    ("Fuel Filter (Tata Sumo)",         320,  450, "pcs",   "Filters",           "slow", 0.8),
    ("Fuel Filter (Tata Ace)",          280,  400, "pcs",   "Filters",           "slow", 0.8),
    ("Fuel Filter (Bolero/Yoddha)",     350,  480, "pcs",   "Filters",           "slow", 0.7),

    # ── Brake System ───────────────────────────────────────────────────────────
    ("Brake Pad Front (Tata Sumo)",    1400, 1800, "set",   "Brake System",      "fast", 1.5),
    ("Brake Pad Front (Bolero)",       1500, 1900, "set",   "Brake System",      "fast", 1.3),
    ("Brake Pad Front (Scorpio)",      1600, 2000, "set",   "Brake System",      "fast", 1.0),
    ("Brake Pad Front (Tata Ace)",     1100, 1450, "set",   "Brake System",      "fast", 1.2),
    ("Brake Lining Rear (Tata Sumo)",   900, 1200, "set",   "Brake System",      "fast", 1.2),
    ("Brake Lining Rear (Bolero)",      950, 1250, "set",   "Brake System",      "fast", 1.0),
    ("Brake Disc (Tata Sumo)",         2200, 2900, "pcs",   "Brake System",      "slow", 0.5),
    ("Brake Disc (Bolero/Scorpio)",    2400, 3100, "pcs",   "Brake System",      "slow", 0.4),
    ("Brake Drum (Tata Ace)",          1800, 2400, "pcs",   "Brake System",      "slow", 0.4),
    ("Brake Drum (Tata Sumo Rear)",    2000, 2600, "pcs",   "Brake System",      "slow", 0.3),
    ("Wheel Cylinder (Tata Sumo)",      750, 1000, "pcs",   "Brake System",      "slow", 0.5),
    ("Wheel Cylinder (Bolero)",         800, 1050, "pcs",   "Brake System",      "slow", 0.4),
    ("Master Cylinder (Tata Sumo)",    1800, 2400, "pcs",   "Brake System",      "slow", 0.3),

    # ── Clutch System ──────────────────────────────────────────────────────────
    ("Clutch Plate (Tata Sumo)",       1800, 2400, "pcs",   "Clutch System",     "slow", 0.6),
    ("Clutch Plate (Bolero/Yoddha)",   1900, 2500, "pcs",   "Clutch System",     "slow", 0.5),
    ("Clutch Plate (Tata Ace)",        1200, 1650, "pcs",   "Clutch System",     "slow", 0.5),
    ("Pressure Plate (Tata Sumo)",     2200, 2900, "pcs",   "Clutch System",     "slow", 0.4),
    ("Pressure Plate (Bolero)",        2400, 3100, "pcs",   "Clutch System",     "slow", 0.3),
    ("Clutch Bearing (Universal)",      480,  650, "pcs",   "Clutch System",     "fast", 0.8),
    ("Clutch Kit (Tata Sumo)",         4500, 5800, "set",   "Clutch System",     "slow", 0.3),
    ("Clutch Kit (Bolero/Yoddha)",     4800, 6200, "set",   "Clutch System",     "slow", 0.2),

    # ── Engine Parts ───────────────────────────────────────────────────────────
    ("Head Gasket (Tata Sumo)",        1200, 1600, "pcs",   "Engine Parts",      "slow", 0.4),
    ("Head Gasket (Bolero)",           1300, 1750, "pcs",   "Engine Parts",      "slow", 0.3),
    ("Head Gasket (Tata Ace)",          850, 1150, "pcs",   "Engine Parts",      "slow", 0.3),
    ("Piston Kit (Tata Sumo)",         3200, 4200, "set",   "Engine Parts",      "slow", 0.2),
    ("Piston Kit (Bolero)",            3500, 4500, "set",   "Engine Parts",      "slow", 0.2),
    ("Piston Ring Set (Tata Sumo)",    1800, 2400, "set",   "Engine Parts",      "slow", 0.3),
    ("Valve Kit (Tata Sumo)",          2200, 2900, "set",   "Engine Parts",      "slow", 0.2),
    ("Timing Belt (Bolero/Scorpio)",   1400, 1900, "pcs",   "Engine Parts",      "slow", 0.4),
    ("Timing Chain (Tata Sumo)",       1600, 2100, "pcs",   "Engine Parts",      "slow", 0.3),
    ("Water Pump (Tata Sumo)",         1800, 2400, "pcs",   "Engine Parts",      "slow", 0.3),
    ("Water Pump (Bolero)",            2000, 2600, "pcs",   "Engine Parts",      "slow", 0.2),
    ("Thermostat (Universal)",          380,  520, "pcs",   "Engine Parts",      "slow", 0.5),
    ("Radiator Hose Upper",             280,  400, "pcs",   "Engine Parts",      "fast", 0.6),
    ("Radiator Hose Lower",             250,  370, "pcs",   "Engine Parts",      "fast", 0.6),
    ("Fan Belt (Tata Sumo)",            380,  520, "pcs",   "Engine Parts",      "fast", 0.8),
    ("Fan Belt (Bolero/Scorpio)",       420,  560, "pcs",   "Engine Parts",      "fast", 0.7),

    # ── Suspension & Steering ──────────────────────────────────────────────────
    ("Shock Absorber Front (Tata Sumo)", 3200, 4200, "pcs", "Suspension",        "slow", 0.4),
    ("Shock Absorber Front (Bolero)",    3500, 4500, "pcs", "Suspension",        "slow", 0.3),
    ("Shock Absorber Rear (Tata Sumo)",  2800, 3700, "pcs", "Suspension",        "slow", 0.4),
    ("Shock Absorber (Tata Ace)",        2200, 2900, "pcs", "Suspension",        "slow", 0.3),
    ("Leaf Spring (Tata Sumo)",          4500, 5800, "pcs", "Suspension",        "slow", 0.2),
    ("Leaf Spring (Tata Ace)",           3200, 4200, "pcs", "Suspension",        "slow", 0.2),
    ("Tie Rod End (Tata Sumo)",           750, 1000, "pcs", "Suspension",        "slow", 0.5),
    ("Tie Rod End (Bolero/Scorpio)",      800, 1050, "pcs", "Suspension",        "slow", 0.4),
    ("Ball Joint (Tata Sumo)",            650,  880, "pcs", "Suspension",        "slow", 0.5),
    ("Ball Joint (Bolero)",               700,  950, "pcs", "Suspension",        "slow", 0.4),
    ("Wheel Bearing Front (Tata Sumo)",   850, 1150, "pcs", "Suspension",        "slow", 0.6),
    ("Wheel Bearing Rear (Tata Sumo)",    780, 1050, "pcs", "Suspension",        "slow", 0.5),
    ("Wheel Bearing (Bolero)",            900, 1200, "pcs", "Suspension",        "slow", 0.5),
    ("King Pin Kit (Tata Sumo)",         1200, 1600, "set", "Suspension",        "slow", 0.3),
    ("Steering Gear Box (Tata Sumo)",    5500, 7200, "pcs", "Suspension",        "slow", 0.1),

    # ── Electrical ─────────────────────────────────────────────────────────────
    ("Battery 12V 60Ah (Exide)",        7200, 9000, "pcs",  "Electrical",        "slow", 0.3),
    ("Battery 12V 80Ah (Exide)",        8800,11000, "pcs",  "Electrical",        "slow", 0.2),
    ("Battery 12V 60Ah (Amaron)",       7500, 9500, "pcs",  "Electrical",        "slow", 0.2),
    ("Headlight Bulb 12V 60W",           120,  180, "pcs",  "Electrical",        "fast", 2.0),
    ("Indicator Bulb 12V",               35,   60, "pcs",   "Electrical",        "fast", 2.5),
    ("Tail Light Bulb",                  45,   75, "pcs",   "Electrical",        "fast", 1.8),
    ("Wiper Blade (Tata Sumo)",          280,  400, "pcs",  "Electrical",        "fast", 0.8),
    ("Wiper Blade (Bolero/Scorpio)",     320,  450, "pcs",  "Electrical",        "fast", 0.7),
    ("Starter Motor (Tata Sumo)",       3800, 5000, "pcs",  "Electrical",        "slow", 0.2),
    ("Alternator (Tata Sumo)",          4500, 5800, "pcs",  "Electrical",        "slow", 0.2),
    ("Fuse Box Set",                     180,  280, "set",  "Electrical",        "fast", 1.0),

    # ── Bearings & Seals ───────────────────────────────────────────────────────
    ("Axle Shaft Seal (Tata Sumo)",      180,  260, "pcs",  "Bearings & Seals",  "fast", 1.0),
    ("Crankshaft Seal Front",            220,  320, "pcs",  "Bearings & Seals",  "fast", 0.8),
    ("Crankshaft Seal Rear",             200,  290, "pcs",  "Bearings & Seals",  "fast", 0.8),
    ("Gearbox Oil Seal",                 150,  220, "pcs",  "Bearings & Seals",  "fast", 0.9),
    ("Wheel Hub Seal (Tata Sumo)",       250,  360, "pcs",  "Bearings & Seals",  "slow", 0.5),
    ("Diff Pinion Bearing",              680,  900, "pcs",  "Bearings & Seals",  "slow", 0.4),
    ("Gearbox Bearing Set",             1200, 1600, "set",  "Bearings & Seals",  "slow", 0.3),

    # ── Body Parts ─────────────────────────────────────────────────────────────
    ("Side Mirror Left (Tata Sumo)",     480,  680, "pcs",  "Body Parts",        "slow", 0.4),
    ("Side Mirror Right (Tata Sumo)",    480,  680, "pcs",  "Body Parts",        "slow", 0.3),
    ("Side Mirror (Bolero)",             520,  720, "pcs",  "Body Parts",        "slow", 0.3),
    ("Bonnet Lock (Tata Sumo)",          380,  540, "pcs",  "Body Parts",        "slow", 0.3),
    ("Door Handle (Tata Sumo)",          220,  320, "pcs",  "Body Parts",        "slow", 0.4),
    ("Bumper Guard (Universal)",         580,  800, "pcs",  "Body Parts",        "slow", 0.2),
    ("Number Plate Light",                80,  130, "pcs",  "Body Parts",        "fast", 0.8),
    ("Radiator Grill (Tata Ace)",        680,  920, "pcs",  "Body Parts",        "slow", 0.2),
]

# ── Suppliers ──────────────────────────────────────────────────────────────────
SUPPLIERS = [
    {"name": "Nepal Auto Distributors",    "phone": "9841234567", "address": "Kathmandu"},
    {"name": "Himalayan Parts Pvt Ltd",    "phone": "9851234567", "address": "Kathmandu"},
    {"name": "Tata Motors Parts Nepal",    "phone": "9861234567", "address": "Lalitpur"},
    {"name": "Mahindra Parts Distributor", "phone": "9871234567", "address": "Kathmandu"},
    {"name": "Singh Auto Suppliers",       "phone": "9881234567", "address": "Bhaktapur"},
]

# ── Customers ──────────────────────────────────────────────────────────────────
# (name, phone, address, customer_type, credit_limit, balance)
CUSTOMERS = [
    # Regular customers
    ("Ram Bahadur Thapa",      "9841111111", "Kathmandu",       "regular",    10000,     0),
    ("Sita Devi Sharma",       "9841222222", "Lalitpur",        "regular",    10000,     0),
    ("Hari Prasad Koirala",    "9841333333", "Bhaktapur",       "regular",    15000,     0),
    ("Gita Kumari Rai",        "9841444444", "Kathmandu",       "regular",    10000,     0),
    ("Bishnu Kumar Tamang",    "9841555555", "Kirtipur",        "regular",    10000,     0),
    ("Maya Devi Gurung",       "9841666666", "Kathmandu",       "regular",    10000,     0),
    ("Kamal Raj Adhikari",     "9841777777", "Lalitpur",        "regular",    10000,     0),
    ("Sunita Shrestha",        "9841888888", "Kathmandu",       "regular",    10000,     0),
    ("Binod Kumar Pandey",     "9841999999", "Bhaktapur",       "regular",    10000,     0),
    ("Rekha Devi Bhattarai",   "9842111111", "Kathmandu",       "regular",    10000,     0),
    ("Deepak Raj Pokhrel",     "9842222222", "Lalitpur",        "regular",    12000,     0),
    ("Anita Kumari Magar",     "9842333333", "Kathmandu",       "regular",    10000,     0),
    ("Prakash Bahadur Limbu",  "9842444444", "Bhaktapur",       "regular",    10000,     0),
    ("Sabita Devi Neupane",    "9842555555", "Kathmandu",       "regular",    10000,     0),
    ("Nabin Raj Bhandari",     "9842666666", "Lalitpur",        "regular",    10000,     0),
    # VIP customers (buy frequently, large amounts)
    ("Puja Kumari Poudel",     "9843111111", "Kathmandu",       "vip",        30000,     0),
    ("Suresh Kumar Karki",     "9843222222", "Lalitpur",        "vip",        30000,     0),
    ("Rita Devi Basnet",       "9843333333", "Bhaktapur",       "vip",        25000,     0),
    ("Arun Bahadur Khadka",    "9843444444", "Kathmandu",       "vip",        30000,     0),
    ("Mina Kumari Chaudhary",  "9843555555", "Kirtipur",        "vip",        25000,     0),
    ("Bikram Singh Tharu",     "9843666666", "Kathmandu",       "vip",        30000,     0),
    ("Laxmi Devi Yadav",       "9843777777", "Lalitpur",        "vip",        25000,     0),
    ("Rajesh Kumar Mandal",    "9843888888", "Kathmandu",       "vip",        30000,     0),
    # Churned customers (bought Jan-Jun 2024, stopped after)
    ("Kamala Devi Jha",        "9844111111", "Kathmandu",       "churned",     5000,     0),
    ("Dinesh Raj Dahal",       "9844222222", "Lalitpur",        "churned",     5000,     0),
    ("Sarita Kumari Regmi",    "9844333333", "Bhaktapur",       "churned",     5000,     0),
    ("Mahesh Bahadur Bohara",  "9844444444", "Kathmandu",       "churned",     5000,     0),
    ("Kiran Kumari Subedi",    "9844555555", "Lalitpur",        "churned",     5000,     0),
    ("Prem Bahadur Ale",       "9844666666", "Kathmandu",       "churned",     5000,     0),
    ("Durga Kumari Bista",     "9844777777", "Bhaktapur",       "churned",     5000,     0),
    ("Santosh Kumar Giri",     "9844888888", "Kathmandu",       "churned",     5000,     0),
    # Bad credit customers (high outstanding udharo)
    ("Kavita Devi Dhakal",     "9845111111", "Kathmandu",       "bad_credit", 20000, 18500),
    ("Narayan Prasad Timilsina","9845222222","Lalitpur",        "bad_credit", 20000, 15200),
    ("Radha Kumari Parajuli",  "9845333333", "Kathmandu",       "bad_credit", 15000, 12800),
    ("Gopal Raj Acharya",      "9845444444", "Bhaktapur",       "bad_credit", 20000, 19000),
    ("Nirmala Devi Chapagain", "9845555555", "Kathmandu",       "bad_credit", 15000, 14200),
    ("Shyam Bahadur Rawat",    "9845666666", "Lalitpur",        "bad_credit", 20000, 17500),
    ("Anju Kumari Khatri",     "9845777777", "Kathmandu",       "bad_credit", 15000, 13800),
]

# ── Festival & Seasonal Multipliers ───────────────────────────────────────────
FESTIVAL_BOOSTS = {
    # Dashain 2024 (Oct 12-15 peak)
    (10,  8): 1.8, (10,  9): 2.2, (10, 10): 2.8,
    (10, 11): 3.5, (10, 12): 4.0, (10, 13): 3.5,
    (10, 14): 2.8, (10, 15): 2.0, (10, 16): 1.5,
    # Tihar 2024 (Oct 29 - Nov 2)
    (10, 28): 1.8, (10, 29): 2.5, (10, 30): 3.0,
    (10, 31): 3.0, (11,  1): 2.5, (11,  2): 2.0,
    # Nepali New Year (Apr 13-14)
    (4,  12): 1.5, (4,  13): 2.0, (4,  14): 1.8,
    # Holi (Mar 25)
    (3,  24): 1.3, (3,  25): 1.5,
    # End of year
    (12, 30): 1.3, (12, 31): 1.5,
    # New Year
    (1,   1): 1.5, (1,   2): 1.3,
    # Maghe Sankranti (Jan 15)
    (1,  14): 1.3, (1,  15): 1.4,
    # Janai Purnima (Aug 19)
    (8,  18): 1.3, (8,  19): 1.5,
}

MONSOON_SLOW = {6, 7, 8}
WEEKEND_MULT = {5: 0.6, 6: 0.0}  # Saturday slow, Sunday closed

def day_multiplier(d: date) -> float:
    if d.weekday() == 6:
        return 0.0
    mult = 1.0
    if (d.month, d.day) in FESTIVAL_BOOSTS:
        mult *= FESTIVAL_BOOSTS[(d.month, d.day)]
    if d.month in MONSOON_SLOW:
        mult *= 0.65
    if d.weekday() == 5:
        mult *= 0.6
    return mult

# ── Insert helpers ─────────────────────────────────────────────────────────────

def setup_categories(store_id):
    print("  Setting up categories...")
    cats = list({p[4] for p in PRODUCTS})
    existing = {r["name"] for r in supabase.table("categories").select("name").eq("store_id", store_id).execute().data}
    new = [{"store_id": store_id, "name": c, "is_system": True} for c in cats if c not in existing]
    if new:
        supabase.table("categories").insert(new).execute()
    return {r["name"]: r["id"] for r in supabase.table("categories").select("id,name").eq("store_id", store_id).execute().data}

def setup_products(store_id, cat_map):
    print(f"  Setting up {len(PRODUCTS)} products...")
    existing = {r["name"] for r in supabase.table("products").select("name").eq("store_id", store_id).execute().data}
    rows = []
    for name, cost, sell, unit, cat, ptype, _ in PRODUCTS:
        if name not in existing:
            rows.append({
                "store_id":       store_id,
                "name":           name,
                "cost_price":     cost,
                "selling_price":  sell,
                "unit":           unit,
                "category_id":    cat_map.get(cat),
                "product_type":   ptype,
                "reorder_level":  5 if ptype == "fast" else 2,
                "stock_quantity": random.randint(15, 80),
                "is_active":      True,
            })
    if rows:
        # Insert in batches of 20
        for i in range(0, len(rows), 20):
            supabase.table("products").insert(rows[i:i+20]).execute()
    return {r["name"]: r for r in supabase.table("products").select("id,name,selling_price,cost_price").eq("store_id", store_id).execute().data}

def setup_suppliers(store_id):
    print("  Setting up suppliers...")
    existing = {r["name"] for r in supabase.table("suppliers").select("name").eq("store_id", store_id).execute().data}
    rows = [{"store_id": store_id, **s} for s in SUPPLIERS if s["name"] not in existing]
    if rows:
        supabase.table("suppliers").insert(rows).execute()
    return {r["name"]: r["id"] for r in supabase.table("suppliers").select("id,name").eq("store_id", store_id).execute().data}

def setup_customers(store_id):
    print(f"  Setting up {len(CUSTOMERS)} customers...")
    existing = {r["name"] for r in supabase.table("customers").select("name").eq("store_id", store_id).execute().data}
    rows = []
    for name, phone, address, ctype, credit_limit, balance in CUSTOMERS:
        if name not in existing:
            rows.append({
                "store_id":     store_id,
                "name":         name,
                "phone":        phone,
                "address":      address,
                "credit_limit": credit_limit,
                "balance":      balance,
            })
    if rows:
        for i in range(0, len(rows), 20):
            supabase.table("customers").insert(rows[i:i+20]).execute()
    result = supabase.table("customers").select("id,name").eq("store_id", store_id).execute().data
    cid_map = {r["name"]: r["id"] for r in result}
    # Build type map
    type_map = {name: ctype for name, _, _, ctype, _, _ in CUSTOMERS}
    ctype_map = {cid_map[name]: ctype for name, ctype in type_map.items() if name in cid_map}
    return cid_map, ctype_map

def generate_sales(store_id, prod_map, cust_map, ctype_map):
    print("  Generating 1 year of sales invoices...")
    prod_daily = {p[0]: p[6] for p in PRODUCTS}
    prod_list  = list(prod_map.values())

    regular_custs = [(n, cid) for n, cid in cust_map.items() if ctype_map.get(cid) in ["regular", "vip", "bad_credit"]]
    churned_custs = [(n, cid) for n, cid in cust_map.items() if ctype_map.get(cid) == "churned"]

    total_invoices = 0
    current        = START_DATE

    while current <= END_DATE:
        mult = day_multiplier(current)
        if mult == 0.0:
            current += timedelta(days=1)
            continue

        # Churned customers only active Jan-May
        if current.month <= 5:
            active = regular_custs + churned_custs
        elif current.month == 6:
            # Churned customers taper off in June
            active = regular_custs + [c for c in churned_custs if random.random() < 0.3]
        else:
            active = regular_custs

        # 4-10 invoices on normal day, more on festival days
        n_inv = max(1, int(random.randint(4, 10) * mult))

        for _ in range(n_inv):
            # 65% known customer, 35% walk-in
            if random.random() < 0.65 and active:
                cname, cid = random.choice(active)
                ctype      = ctype_map.get(cid, "regular")
            else:
                cid        = None
                ctype      = "walkin"

            # Payment method
            if ctype == "bad_credit":
                payment = random.choices(["credit","cash"], weights=[0.65, 0.35])[0]
            elif ctype == "vip":
                payment = random.choices(["cash","card","esewa","khalti"], weights=[0.4,0.2,0.2,0.2])[0]
            elif ctype == "churned":
                payment = random.choices(["cash","credit"], weights=[0.75, 0.25])[0]
            else:
                payment = random.choices(["cash","esewa","khalti","card","credit","bank_transfer"],
                                         weights=[0.50, 0.15, 0.10, 0.10, 0.10, 0.05])[0]

            # Pick 1-4 products
            n_prods   = random.choices([1,2,3,4], weights=[0.45, 0.30, 0.18, 0.07])[0]
            selected  = random.sample(prod_list, min(n_prods, len(prod_list)))
            items     = []
            subtotal  = 0

            for prod in selected:
                avg   = prod_daily.get(prod["name"], 1.0)
                qty   = max(1, round(random.gauss(avg, avg * 0.4) * max(1.0, mult)))
                qty   = min(qty, 15)
                price = prod["selling_price"]
                disc  = random.choice([0, 0, 0, 100, 200]) if random.random() < 0.08 else 0
                itot  = max(0, qty * price - disc)
                subtotal += itot
                items.append({
                    "product_id":   prod["id"],
                    "product_name": prod["name"],
                    "quantity":     qty,
                    "unit_price":   price,
                    "discount":     disc,
                    "total":        round(itot, 2),
                })

            # Invoice-level discount for large orders
            inv_disc = 0
            if subtotal > 5000 and random.random() < 0.2:
                inv_disc = random.choice([200, 300, 500])

            total  = max(0, subtotal - inv_disc)
            status = "paid" if payment != "credit" else "unpaid"
            invnum = f"INV-{current.strftime('%Y%m%d')}-{total_invoices:04d}"

            inv = supabase.table("invoices").insert({
                "store_id":       store_id,
                "customer_id":    cid,
                "invoice_number": invnum,
                "invoice_date":   str(current),
                "subtotal":       round(subtotal, 2),
                "discount":       inv_disc,
                "tax":            0,
                "total":          round(total, 2),
                "paid_amount":    round(total, 2) if status == "paid" else 0,
                "payment_method": payment,
                "status":         status,
            }).execute().data[0]

            for item in items:
                item["invoice_id"] = inv["id"]
            # Insert items in one batch
            supabase.table("invoice_items").insert(items).execute()
            total_invoices += 1

        current += timedelta(days=1)

    print(f"  Created {total_invoices} invoices")
    return total_invoices

def generate_purchases(store_id, prod_map, supplier_map):
    print("  Generating purchase records...")
    prod_list = list(prod_map.values())
    suppliers = list(supplier_map.items())
    count     = 0

    for month in range(1, 13):
        # 3-5 purchase bills per month
        n_purchases = random.randint(3, 5)
        for _ in range(n_purchases):
            day = random.randint(1, 25)
            try:
                purchase_date = date(2024, month, day)
            except:
                purchase_date = date(2024, month, 1)

            sup_name, sup_id = random.choice(suppliers)
            # 3-8 products per purchase
            n_prods  = random.randint(3, 8)
            selected = random.sample(prod_list, min(n_prods, len(prod_list)))
            items    = []
            subtotal = 0

            for prod in selected:
                qty   = random.randint(5, 30)
                cost  = prod_map[prod["name"]]["cost_price"] if prod["name"] in prod_map else 500
                itot  = qty * cost
                subtotal += itot
                items.append({
                    "product_id":   prod["id"],
                    "product_name": prod["name"],
                    "quantity":     qty,
                    "unit_price":   cost,
                    "total":        round(itot, 2),
                })

            # Delivery charge on some purchases
            delivery = random.choice([0, 0, 200, 300, 500]) if random.random() < 0.3 else 0
            total    = subtotal + delivery

            pur = supabase.table("purchases").insert({
                "store_id":       store_id,
                "supplier_id":    sup_id,
                "bill_number":    f"BILL-{month:02d}-{count:03d}",
                "purchase_date":  str(purchase_date),
                "subtotal":       round(subtotal, 2),
                "tax":            0,
                "delivery_charge": delivery,
                "total":          round(total, 2),
                "paid_amount":    round(total, 2),
                "status":         "paid",
            }).execute().data[0]

            for item in items:
                item["purchase_id"] = pur["id"]
            supabase.table("purchase_items").insert(items).execute()
            count += 1

    print(f"  Created {count} purchase bills")

def generate_expenses(store_id):
    print("  Generating monthly expenses...")
    count = 0
    for month in range(1, 13):
        for cat, min_amt, max_amt in [
            ("Rent",          15000, 15000),
            ("Salary",        22000, 22000),
            ("Electricity",    2500,  4500),
            ("Water",           500,   800),
            ("Transport",      1500,  3500),
            ("Telephone",       800,  1500),
            ("Miscellaneous",  1000,  3000),
        ]:
            amount = random.randint(min_amt, max_amt)
            day    = random.randint(1, 5) if cat in ["Rent", "Salary"] else random.randint(1, 28)
            try:
                exp_date = date(2024, month, day)
            except:
                exp_date = date(2024, month, 1)

            supabase.table("expenses").insert({
                "store_id":     store_id,
                "category":     cat,
                "amount":       amount,
                "description":  f"{cat} for {exp_date.strftime('%B %Y')}",
                "expense_date": str(exp_date),
            }).execute()
            count += 1

    print(f"  Created {count} expense records")

def generate_khata(store_id, cust_map, ctype_map):
    print("  Generating khata/udharo entries...")
    count = 0
    for cname, cid in cust_map.items():
        ctype = ctype_map.get(cid, "regular")
        if ctype == "bad_credit":
            # Multiple credit entries, few payments
            n_debits = random.randint(8, 15)
            for i in range(n_debits):
                month = random.randint(1, 10)
                day   = random.randint(1, 28)
                supabase.table("khata_entries").insert({
                    "store_id":   store_id,
                    "party_type": "customer",
                    "party_id":   cid,
                    "entry_type": "debit",
                    "amount":     random.randint(1500, 5000),
                    "description":"Goods sold on credit",
                    "entry_date": str(date(2024, month, day)),
                }).execute()
                count += 1
            # Only 1-2 partial payments
            n_credits = random.randint(1, 2)
            for i in range(n_credits):
                month = random.randint(2, 11)
                day   = random.randint(1, 28)
                supabase.table("khata_entries").insert({
                    "store_id":   store_id,
                    "party_type": "customer",
                    "party_id":   cid,
                    "entry_type": "credit",
                    "amount":     random.randint(1000, 3000),
                    "description":"Partial payment received",
                    "entry_date": str(date(2024, month, day)),
                }).execute()
                count += 1

        elif ctype == "vip":
            # Regular credit and prompt payments
            n_entries = random.randint(4, 8)
            for i in range(n_entries):
                month = random.randint(1, 12)
                day   = random.randint(1, 28)
                amt   = random.randint(2000, 8000)
                supabase.table("khata_entries").insert({
                    "store_id":   store_id,
                    "party_type": "customer",
                    "party_id":   cid,
                    "entry_type": "debit",
                    "amount":     amt,
                    "description":"Goods sold on credit",
                    "entry_date": str(date(2024, month, day)),
                }).execute()
                # Pays within 2 weeks
                pay_day = min(day + random.randint(5, 14), 28)
                supabase.table("khata_entries").insert({
                    "store_id":   store_id,
                    "party_type": "customer",
                    "party_id":   cid,
                    "entry_type": "credit",
                    "amount":     amt,
                    "description":"Payment received",
                    "entry_date": str(date(2024, month, pay_day)),
                }).execute()
                count += 2

    print(f"  Created {count} khata entries")

def add_anomalies(store_id, prod_map):
    """Add 6 anomalous transactions for anomaly detection training"""
    print("  Adding anomaly transactions...")
    prod_list = list(prod_map.values())
    anomalies = [
        # (description, total_multiplier, date)
        ("Unusually large single transaction", 15, date(2024, 3, 15)),
        ("Duplicate invoice same day",          1, date(2024, 5, 22)),
        ("Suspiciously round amount",           8, date(2024, 7, 10)),
        ("Large discount transaction",         10, date(2024, 9,  5)),
        ("Off-hours transaction",               6, date(2024, 11, 18)),
        ("Abnormal quantity order",            12, date(2024, 12,  3)),
    ]

    for desc, mult, inv_date in anomalies:
        prod = random.choice(prod_list)
        qty  = max(1, int(5 * mult))
        price = prod["selling_price"]
        total = qty * price

        inv = supabase.table("invoices").insert({
            "store_id":       store_id,
            "customer_id":    None,
            "invoice_number": f"ANOM-{inv_date.strftime('%Y%m%d')}",
            "invoice_date":   str(inv_date),
            "subtotal":       round(total, 2),
            "discount":       0,
            "tax":            0,
            "total":          round(total, 2),
            "paid_amount":    round(total, 2),
            "payment_method": "cash",
            "status":         "paid",
            "notes":          f"ANOMALY: {desc}",
        }).execute().data[0]

        supabase.table("invoice_items").insert({
            "invoice_id":   inv["id"],
            "product_id":   prod["id"],
            "product_name": prod["name"],
            "quantity":     qty,
            "unit_price":   price,
            "discount":     0,
            "total":        round(total, 2),
        }).execute()

    print("  Added 6 anomaly transactions")

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("RetailSense Nepal - Data Generator")
    print("Bijeta Auto Parts - Commercial Vehicle Parts")
    print("=" * 60)

    print("\n[1/8] Getting store ID...")
    store_id = get_store_id()
    print(f"      Store ID: {store_id}")

    print("\n[2/8] Setting up categories...")
    cat_map  = setup_categories(store_id)
    print(f"      {len(cat_map)} categories ready")

    print("\n[3/8] Setting up products...")
    prod_map = setup_products(store_id, cat_map)
    print(f"      {len(prod_map)} products ready")

    print("\n[4/8] Setting up suppliers...")
    sup_map  = setup_suppliers(store_id)
    print(f"      {len(sup_map)} suppliers ready")

    print("\n[5/8] Setting up customers...")
    cust_map, ctype_map = setup_customers(store_id)
    print(f"      {len(cust_map)} customers ready")

    print("\n[6/8] Generating 1 year of sales (Jan-Dec 2024)...")
    print("      This may take 3-5 minutes...")
    n_inv = generate_sales(store_id, prod_map, cust_map, ctype_map)

    print("\n[7/8] Generating purchase records...")
    generate_purchases(store_id, prod_map, sup_map)

    print("\n[8/8] Generating expenses and khata entries...")
    generate_expenses(store_id)
    generate_khata(store_id, cust_map, ctype_map)
    add_anomalies(store_id, prod_map)

    print("\n" + "=" * 60)
    print("Data generation complete!")
    print(f"  Products:   {len(prod_map)}")
    print(f"  Customers:  {len(cust_map)}")
    print(f"  Suppliers:  {len(sup_map)}")
    print(f"  Invoices:   ~{n_inv}")
    print(f"  Categories: {len(cat_map)}")
    print("=" * 60)
    print("\nYou can now train the AI models.")

if __name__ == "__main__":
    main()
