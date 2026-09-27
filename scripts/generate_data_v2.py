"""
RetailSense Nepal - Synthetic Data Generator v2
Bijeta Auto Parts - Auto Parts Retail Store (Kathmandu)

Aligned to the CURRENT schema (as of the discount/cost-snapshot fixes):
  - purchases: subtotal, discount_total, tax, total, paid_amount, status
  - purchase_items: discount_percent (NOT flat discount)
  - invoices: subtotal, discount (flat Rs), tax, delivery_charge, total, paid_amount
  - invoice_items: discount (flat Rs, NOT percent), cost_price_at_sale
  - payments + payment_allocations for partial/full customer paydowns
  - products: cost_price, list_price, previous_cost_price, selling_price,
    stock_quantity, reorder_level, product_type, is_active, category_id

Time range: Sep 2, 2025 -> Sep 1, 2026 (12 months, ending "today").
Dashain 2025 window: Sep 22 - Oct 6, 2025 (Tika Oct 2) - real dates.

Run: python scripts/generate_data_v2.py
Dry run (no DB writes, just prints stats): python scripts/generate_data_v2.py --dry-run
"""

import random
import sys
from datetime import date, timedelta
from collections import defaultdict

DRY_RUN = "--dry-run" in sys.argv

if not DRY_RUN:
    from supabase import create_client
    from dotenv import load_dotenv
    import os
    load_dotenv()
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

STORE_NAME = "Bijeta Auto Parts"
START_DATE = date(2025, 9, 2)
END_DATE   = date(2026, 9, 1)
random.seed(7)

# ============================================================
# STORE / STAFF LOOKUP
# ============================================================

def get_store_id():
    r = supabase.table("stores").select("id").eq("name", STORE_NAME).single().execute()
    if not r.data:
        raise ValueError(f"Store '{STORE_NAME}' not found. Create it first via signup.")
    return r.data["id"]


# ============================================================
# PRODUCTS - 1,100+ real-style part names across all requested models
# ============================================================

MODELS = ["Tata Sumo", "Tata Ace", "Tata Mega", "Tata Intra", "Tata 207 DI",
          "Tata Winger", "Tata Yodha", "Mahindra Bolero", "Mahindra Bolero Camper",
          "Mahindra Scorpio", "Mahindra Maximo"]

CATEGORY_DEFAULTS = {
    # category: (cost_low, cost_high, unit, product_type)
    "Filters":            (150, 550, "pcs", "fast"),
    "Clutch System":       (300, 6500, "pcs", "slow"),
    "Engine Parts":        (150, 4800, "pcs", "slow"),
    "Suspension":          (250, 8200, "pcs", "slow"),
    "Drivetrain":          (250, 6500, "pcs", "slow"),
    "Bearings & Seals":    (90, 1900, "pcs", "fast"),
    "Electrical":          (25, 10500, "pcs", "slow"),
    "Body Parts":          (60, 1100, "pcs", "slow"),
    "Brake System":        (500, 3000, "set", "fast"),
}

UNSIDED_PARTS = [
    ("Air Filter","Filters"),("Oil Filter","Filters"),("Fuel Filter","Filters"),
    ("Diesel Filter","Filters"),("Cabin Air Filter","Filters"),
    ("Clutch Plate","Clutch System"),("Pressure Plate","Clutch System"),
    ("Clutch Bearing","Clutch System"),("Clutch Kit Full Set","Clutch System"),
    ("Clutch Release Bearing","Clutch System"),
    ("Head Gasket","Engine Parts"),("Piston Kit","Engine Parts"),
    ("Piston Ring Set","Engine Parts"),("Valve Kit","Engine Parts"),
    ("Timing Belt","Engine Parts"),("Timing Chain","Engine Parts"),
    ("Water Pump","Engine Parts"),("Thermostat","Engine Parts"),
    ("Radiator Assembly","Engine Parts"),("Radiator Hose Upper","Engine Parts"),
    ("Radiator Hose Lower","Engine Parts"),("Fan Belt","Engine Parts"),
    ("Fan Motor Assembly","Engine Parts"),("Engine Mounting","Engine Parts"),
    ("Gearbox Mounting","Engine Parts"),("Turbo Charger Assembly","Engine Parts"),
    ("Injector Nozzle","Engine Parts"),("Fuel Pump Assembly","Engine Parts"),
    ("Accelerator Cable End","Engine Parts"),
    ("Leaf Spring Main","Suspension"),("Leaf Spring Bolt Set 6 inch","Suspension"),
    ("Leaf Spring Bolt Set 7 inch","Suspension"),("Tie Rod End","Suspension"),
    ("Ball Joint Upper","Suspension"),("Ball Joint Lower","Suspension"),
    ("King Pin Kit","Suspension"),("Steering Gear Box","Suspension"),
    ("Power Steering Pump Assembly","Suspension"),("Power Steering Kit Full Set","Suspension"),
    ("Front Suspension Bush Kit","Suspension"),("Steering Cross","Suspension"),
    ("CV Joint","Drivetrain"),("Propeller Shaft","Drivetrain"),
    ("Differential Gasket","Drivetrain"),("Center Link Assembly","Drivetrain"),
    ("Drive Shaft Assembly","Drivetrain"),("Crown Pinion","Drivetrain"),
    ("Diff Star Pin","Drivetrain"),
    ("Gearbox Bearing Set","Bearings & Seals"),("Diff Pinion Bearing","Bearings & Seals"),
    ("Crankshaft Seal Front","Bearings & Seals"),("Crankshaft Seal Rear","Bearings & Seals"),
    ("Gearbox Oil Seal","Bearings & Seals"),
    ("Battery 12V 60Ah","Electrical"),("Battery 12V 80Ah","Electrical"),
    ("Starter Motor","Electrical"),("Alternator","Electrical"),
    ("Horn Assembly","Electrical"),("Wiper Motor Assembly","Electrical"),
    ("Temperature Sensor","Electrical"),
    ("Body Mounting Kit","Body Parts"),("Bonnet Lock","Body Parts"),
    ("Radiator Grill","Body Parts"),("Center Sliding Door Lock","Body Parts"),
    ("Dala Diki Handle","Body Parts"),
]

SIDED_PARTS = [
    ("Brake Pad Set","Brake System",("Front","Rear")),
    ("Brake Lining Set","Brake System",("Front","Rear")),
    ("Brake Disc Rotor","Brake System",("Front","Rear")),
    ("Brake Drum","Brake System",("Front","Rear")),
    ("Wheel Cylinder","Brake System",("Front","Rear")),
    ("Shock Absorber","Suspension",("Front","Rear")),
    ("Wheel Bearing","Suspension",("Front","Rear")),
    ("Axle Oil Seal","Bearings & Seals",("Inner","Outer")),
    ("Side Mirror","Body Parts",("Left","Right")),
    ("Door Handle","Body Parts",("Left","Right")),
    ("Headlight Assembly","Electrical",("Left","Right")),
    ("Tail Light Assembly","Electrical",("Left","Right")),
    ("Indicator Light Assembly","Electrical",("Left","Right")),
    ("Brake Assembly","Brake System",("LH","RH")),
    ("Lock Door Front","Body Parts",("LH","RH")),
    ("Axle Spacer","Suspension",("Front","Rear")),
    ("Wheel Hub Assembly","Suspension",("Front","Rear")),
    ("Master Cylinder Assembly","Brake System",("Front","Rear")),
]

UNIVERSAL_PARTS = [
    # (name, category, cost_low, cost_high, unit, product_type)
    ("Hex Bolt 10x1.5x25","Fasteners",30,90,"pcs","fast"),
    ("Hex Bolt 10x1.5x35","Fasteners",35,100,"pcs","fast"),
    ("Hex Bolt 12x1.5x50","Fasteners",45,120,"pcs","fast"),
    ("Nyloc Nut 10x1.25","Fasteners",8,25,"pcs","fast"),
    ("Hex Nut 10x1.5","Fasteners",6,20,"pcs","fast"),
    ("Hex Nut 12x1.5","Fasteners",8,25,"pcs","fast"),
    ("Lock Washer 10mm","Fasteners",5,15,"pcs","fast"),
    ("Lock Washer 12mm","Fasteners",6,18,"pcs","fast"),
    ("Spring Pin Hex Head with Lock Nut","Fasteners",40,120,"pcs","fast"),
    ("Leaf Spring U-Bolt Set","Fasteners",250,600,"set","fast"),
    ("Fuse Box Set","Electrical",150,320,"set","fast"),
    ("Wiper Blade Universal","Electrical",220,480,"pcs","fast"),
    ("Headlight Bulb 12V 60W","Electrical",80,180,"pcs","fast"),
    ("Indicator Bulb 12V","Electrical",25,65,"pcs","fast"),
    ("Tail Light Bulb","Electrical",35,80,"pcs","fast"),
    ("Number Plate Light","Electrical",60,150,"pcs","fast"),
    ("Engine Oil CH4 1L","Lubricants & Oils",480,650,"litre","fast"),
    ("Engine Oil CI4 1L","Lubricants & Oils",550,720,"litre","fast"),
    ("Gear Oil 90 1L","Lubricants & Oils",350,480,"litre","slow"),
    ("Coolant 1L","Lubricants & Oils",260,380,"litre","fast"),
    ("Brake Oil DOT3 500ml","Lubricants & Oils",85,140,"bottle","fast"),
    ("Grease 500g","Lubricants & Oils",190,280,"tin","slow"),
    ("Radiator Coolant Sealant","Lubricants & Oils",150,280,"bottle","slow"),
    ("Silicone Gasket Maker","Lubricants & Oils",180,320,"tube","slow"),
    ("Anti-Rust Spray","Lubricants & Oils",220,380,"can","slow"),
]

# Sumo parts get a mild bump in Dashain-season demand per the store's own
# observation (used later as a per-product sales-weight multiplier).
SUMO_KEYWORD = "Tata Sumo"


def build_products():
    """Returns list of dicts (without store_id/category_id yet)."""
    rows = []
    for name, cat in UNSIDED_PARTS:
        low, high, unit, ptype = CATEGORY_DEFAULTS[cat]
        for m in MODELS:
            cost = round(random.uniform(low, high), 2)
            rows.append({
                "name": f"{name} ({m})",
                "category": cat,
                "cost_price": cost,
                "unit": "set" if "Kit" in name or "Set" in name else unit,
                "product_type": ptype,
            })
    for name, cat, sides in SIDED_PARTS:
        low, high, unit, ptype = CATEGORY_DEFAULTS[cat]
        for m in MODELS:
            for s in sides:
                cost = round(random.uniform(low, high), 2)
                rows.append({
                    "name": f"{name} {s} ({m})",
                    "category": cat,
                    "cost_price": cost,
                    "unit": "set" if "Kit" in name or "Set" in name else unit,
                    "product_type": ptype,
                })
    for name, cat, low, high, unit, ptype in UNIVERSAL_PARTS:
        cost = round(random.uniform(low, high), 2)
        rows.append({
            "name": name, "category": cat, "cost_price": cost,
            "unit": unit, "product_type": ptype,
        })

    # selling_price / list_price with a realistic margin (25-45%)
    for r in rows:
        margin = random.uniform(0.25, 0.45)
        r["selling_price"] = round(r["cost_price"] * (1 + margin), 2)
        r["list_price"] = round(r["cost_price"] * random.uniform(1.4, 1.9), 2)  # pre-discount gross reference
        r["reorder_level"] = 5 if r["product_type"] == "fast" else 2
        r["stock_quantity"] = random.randint(10, 90)
        r["is_active"] = True
        r["is_sumo"] = SUMO_KEYWORD in r["name"]
    return rows


# ============================================================
# SUPPLIERS - 36 total, weighted long tail matching real proportions
# ============================================================

SUPPLIERS = [
    # (name, phone, address, weight) - weight drives purchase-bill share
    ("Sipradi Auto Parts Pvt. Ltd.",              "0514-11032",  "Birgunj, Nepal",        40),
    ("Rita Automobiles Pvt. Ltd.",                "9841112233",  "Kathmandu",              14),
    ("Gautam Buddha Auto Spares Pvt. Ltd.",       "9851122334",  "Dakshinkali, Kathmandu", 8),
    ("BNH Auto Tech Pvt Ltd",                     "9861133445",  "Kathmandu",              6),
    ("Bajrasatya Traders",                        "9871144556",  "Kathmandu",              5),
    ("Satyadip International Pvt. Ltd.",          "9881155667",  "Kathmandu",              4),
    ("Zoya Trade Concern Pvt. Ltd.",               "9811166778",  "Kathmandu",              3),
    ("Ladali International Pvt. Ltd.",             "9821177889",  "Kathmandu",              3),
    ("Royal Auto Parts",                          "9831188990",  "Kathmandu",              2.5),
    ("Darsan Trade Link Pvt. Ltd.",                "9841199001",  "Kathmandu",              2.5),
    ("Mahavir Auto Spares Pvt. Ltd.",              "9851100112",  "Kathmandu",              3),
    ("SDI Auto Mobiles Pvt. Ltd.",                 "9861111223",  "Kathmandu",              1.5),
    ("S.E. International Pvt. Ltd.",               "9871122334",  "Balkumari, Lalitpur",    1.5),
    ("Auto Zone Traders",                         "9881133445",  "Kathmandu",              1),
    ("Best Buy Auto Pvt. Ltd.",                    "9811144556",  "Kathmandu",              1),
    ("Manakamana International",                  "9821155667",  "Kathmandu",              1),
    ("Good Automobile Pvt. Ltd.",                  "9831166778",  "Kathmandu",              1),
    ("Auto Spares Nepal Pvt. Ltd.",                "9841177889",  "Kathmandu",              1),
    ("Nepal Auto Parts Group Pvt. Ltd.",           "9851188990",  "Kathmandu",              1),
    ("Mahakali Automobiles Pvt. Ltd.",             "9861199001",  "Kathmandu",              0.8),
    ("Unique Enterprises & Traders",              "9871100112",  "Kathmandu",              0.8),
    ("Kapish Trade Concern",                      "9881111223",  "Kathmandu",              0.6),
    ("New Kasaju Enterprises",                    "9811122334",  "Kathmandu",              0.6),
    ("Priyadip Traders",                          "9821133445",  "Kathmandu",              0.6),
    ("Next Generation Automotive Pvt. Ltd.",      "9831144556",  "Kathmandu",              0.6),
    ("Himalayan Auto Distributors",                "9841155667",  "Kathmandu",              0.5),
    ("Everest Motor Spares Pvt. Ltd.",             "9851166778",  "Kathmandu",              0.5),
    ("Annapurna Auto Traders",                    "9861177889",  "Pokhara",                0.5),
    ("Machhapuchhre Auto Parts",                  "9871188990",  "Pokhara",                0.5),
    ("Bagmati Trade Concern",                     "9881199001",  "Kathmandu",              0.4),
    ("Sagarmatha Auto Spares",                    "9811100112",  "Kathmandu",              0.4),
    ("Trishuli Motor Traders",                    "9821111223",  "Kathmandu",              0.4),
    ("Gorkha Auto Distributors",                  "9831122334",  "Kathmandu",              0.4),
    ("Lumbini Auto Parts Concern",                "9841133445",  "Bhairahawa",             0.3),
    ("Koshi Auto Spares Pvt. Ltd.",                "9851144556",  "Biratnagar",             0.3),
    ("Karnali Trade Link",                        "9861155667",  "Kathmandu",              0.3),
]
assert len(SUPPLIERS) >= 35


# ============================================================
# CUSTOMERS - 50 total: ~15 recurring B2B workshops + ~35 individuals
# ============================================================

WORKSHOP_CUSTOMERS = [
    "Solukhumbu Auto Work Shop", "Gita Auto Workshop", "Bishwakarma Motor Parts",
    "Halesi Parts", "Bhaktapur Motor Works", "Kirtipur Auto Garage",
    "Kalanki Auto Service Center", "Balaju Motor Workshop", "Naya Bazar Auto Repair",
    "Chabahil Garage & Spares", "Koteshwor Auto Works", "Gongabu Bus Park Motors",
    "Thankot Highway Garage", "Suryabinayak Auto Center", "Sanepa Motor Workshop",
]

# Diverse Nepali given/surnames spanning Janajati (Tamang, Gurung, Magar, Rai,
# Limbu, Sherpa, Thami), Brahmin/Chhetri, Newar, and Madhesi communities -
# reflecting the customer mix described, without storing any ethnicity field.
GIVEN_NAMES_M = ["Bikash","Sanjay","Prakash","Dilip","Kumar","Nabin","Suman","Arun",
                 "Rajan","Bishnu","Dorje","Pemba","Tenzing","Lal Bahadur","Man Bahadur",
                 "Krishna","Ramesh","Deepak","Sunil","Anil"]
GIVEN_NAMES_F = ["Sita","Gita","Maya","Anita","Sunita","Kamala","Radha","Nirmala",
                 "Sabita","Rekha","Pemba","Doma","Yangzom","Laxmi","Sarita",
                 "Kavita","Mina","Bimala","Sarmila","Sangita"]
SURNAMES = ["Tamang","Gurung","Magar","Rai","Limbu","Sherpa","Thami","Waiba",
            "Sharma","Koirala","Adhikari","Poudel","Khadka","Basnet","Karki",
            "Shrestha","Maharjan","Shakya","Manandhar","Pradhan",
            "Yadav","Mandal","Jha","Chaudhary","Thakur"]

def random_individual_name():
    surname = random.choice(SURNAMES)
    given = random.choice(GIVEN_NAMES_M if random.random() < 0.55 else GIVEN_NAMES_F)
    return f"{given} {surname}"


# ============================================================
# CALENDAR / SEASONALITY
# ============================================================

# Dashain 2025: Ghatasthapana Sep 22 -> Kojagrat Purnima Oct 6, Tika Oct 2 (real dates)
FESTIVAL_BOOSTS = {
    (9, 20): 1.3, (9, 21): 1.4,
    (9, 22): 1.8, (9, 23): 1.9, (9, 24): 2.0, (9, 25): 2.1, (9, 26): 2.2,
    (9, 27): 2.3, (9, 28): 2.5, (9, 29): 2.7, (9, 30): 3.0,
    (10, 1): 3.4, (10, 2): 3.6, (10, 3): 2.8, (10, 4): 2.2,
    (10, 5): 1.8, (10, 6): 1.5,
    # Tihar ~2-3 weeks after Dashain (approx)
    (10, 18): 1.5, (10, 19): 1.8, (10, 20): 2.0, (10, 21): 1.8, (10, 22): 1.4,
    # Nepali New Year (mid-April, approx for 2082->2083 transition)
    (4, 13): 1.4, (4, 14): 1.6,
    # Holi (approx)
    (3, 13): 1.2, (3, 14): 1.3,
    # Maghe Sankranti
    (1, 14): 1.2, (1, 15): 1.3,
}
MONSOON_SLOW = {6, 7, 8}   # Jun-Aug: slower vehicle usage / fewer breakdowns


def day_multiplier(d: date) -> float:
    if d.weekday() == 6:   # Sunday closed
        return 0.0
    mult = 1.0
    if (d.month, d.day) in FESTIVAL_BOOSTS:
        mult *= FESTIVAL_BOOSTS[(d.month, d.day)]
    if d.month in MONSOON_SLOW:
        mult *= 0.85
    if d.weekday() == 5:   # Saturday slightly slower
        mult *= 0.85
    return mult


def is_dashain_window(d: date) -> bool:
    return (d.month, d.day) in FESTIVAL_BOOSTS and d.month in (9, 10) and d.day <= 6 or (d.month == 9 and d.day >= 20)


# ============================================================
# DRY-RUN STATS (no DB writes - sanity check the math)
# ============================================================

def dry_run():
    products = build_products()
    print(f"Products generated: {len(products)}")
    print(f"Suppliers: {len(SUPPLIERS)}")
    print(f"Customers: {len(WORKSHOP_CUSTOMERS)} workshops + 35 individuals = {len(WORKSHOP_CUSTOMERS)+35}")

    current = START_DATE
    daily_totals = []
    while current <= END_DATE:
        mult = day_multiplier(current)
        if mult == 0.0:
            current += timedelta(days=1); continue
        base = random.uniform(70000, 85000)
        daily_totals.append(base * mult)
        current += timedelta(days=1)

    normal_days = [d for d in daily_totals if d < 140000]
    festival_days = [d for d in daily_totals if d >= 140000]
    print(f"\nSimulated trading days: {len(daily_totals)}")
    print(f"Normal-day avg: Rs {sum(normal_days)/len(normal_days):,.0f}  (target 70-85k)")
    if festival_days:
        print(f"Festival-day avg: Rs {sum(festival_days)/len(festival_days):,.0f}  (target 150-200k)")
        print(f"Festival days count: {len(festival_days)}")
    print(f"Annual projected revenue: Rs {sum(daily_totals):,.0f}")

    print(f"\nPurchase bills target: 300 across {len(SUPPLIERS)} suppliers")
    weights = [s[3] for s in SUPPLIERS]
    total_w = sum(weights)
    sipradi_share = weights[0] / total_w
    print(f"Sipradi's expected share of purchase bills: {sipradi_share*100:.1f}%")


if DRY_RUN:
    if __name__ == "__main__":
        dry_run()
        sys.exit(0)


# ============================================================
# LIVE DB WRITE HELPERS (only used when NOT --dry-run)
# ============================================================

def batch_insert(table, rows, size=25):
    for i in range(0, len(rows), size):
        supabase.table(table).insert(rows[i:i+size]).execute()


def setup_categories(store_id, products):
    print("  Setting up categories...")
    cats = sorted({p["category"] for p in products})
    existing = {r["name"] for r in supabase.table("categories").select("name").eq("store_id", store_id).execute().data}
    new = [{"store_id": store_id, "name": c, "is_system": True} for c in cats if c not in existing]
    if new:
        batch_insert("categories", new)
    return {r["name"]: r["id"] for r in supabase.table("categories").select("id,name").eq("store_id", store_id).execute().data}


def setup_products(store_id, products, cat_map):
    print(f"  Setting up {len(products)} products...")
    existing = {r["name"] for r in supabase.table("products").select("name").eq("store_id", store_id).execute().data}
    rows = []
    for p in products:
        if p["name"] in existing:
            continue
        rows.append({
            "store_id":       store_id,
            "name":           p["name"],
            "unit":           p["unit"],
            "cost_price":     p["cost_price"],
            "list_price":     p["list_price"],
            "selling_price":  p["selling_price"],
            "category_id":    cat_map.get(p["category"]),
            "product_type":   p["product_type"],
            "reorder_level":  p["reorder_level"],
            "stock_quantity": p["stock_quantity"],
            "is_active":      True,
        })
    if rows:
        batch_insert("products", rows)
    result = supabase.table("products").select("id,name,cost_price,selling_price,unit").eq("store_id", store_id).execute().data
    prod_map = {r["name"]: r for r in result}
    for p in products:
        if p["name"] in prod_map:
            prod_map[p["name"]]["is_sumo"] = p["is_sumo"]
    return prod_map


def setup_suppliers(store_id):
    print(f"  Setting up {len(SUPPLIERS)} suppliers...")
    existing = {r["name"] for r in supabase.table("suppliers").select("name").eq("store_id", store_id).execute().data}
    rows = []
    for name, phone, address, weight in SUPPLIERS:
        if name not in existing:
            rows.append({"store_id": store_id, "name": name, "phone": phone, "address": address})
    if rows:
        try:
            batch_insert("suppliers", rows)
        except Exception as e:
            print(f"    (retrying suppliers without 'address' - {e})")
            for r in rows: r.pop("address", None)
            batch_insert("suppliers", rows)
    result = supabase.table("suppliers").select("id,name").eq("store_id", store_id).execute().data
    name_to_id = {r["name"]: r["id"] for r in result}
    weight_map = {name: w for name, _, _, w in SUPPLIERS}
    return [(name_to_id[n], weight_map[n]) for n in name_to_id if n in weight_map]


def setup_customers(store_id):
    n_individuals = 35
    print(f"  Setting up {len(WORKSHOP_CUSTOMERS)} workshop + {n_individuals} individual customers...")
    existing = {r["name"] for r in supabase.table("customers").select("name").eq("store_id", store_id).execute().data}

    rows = []
    for name in WORKSHOP_CUSTOMERS:
        if name not in existing:
            rows.append({"store_id": store_id, "name": name, "phone": f"98{random.randint(10000000,49999999)}",
                         "credit_limit": random.choice([20000, 30000, 50000]), "balance": 0})

    individual_names = set()
    while len(individual_names) < n_individuals:
        individual_names.add(random_individual_name())
    for name in individual_names:
        if name not in existing:
            rows.append({"store_id": store_id, "name": name, "phone": f"98{random.randint(10000000,49999999)}",
                         "credit_limit": random.choice([0, 5000, 10000]), "balance": 0})

    if rows:
        try:
            batch_insert("customers", rows)
        except Exception as e:
            print(f"    (retrying customers without credit_limit - {e})")
            for r in rows: r.pop("credit_limit", None)
            batch_insert("customers", rows)

    result = supabase.table("customers").select("id,name").eq("store_id", store_id).execute().data
    name_to_id = {r["name"]: r["id"] for r in result}
    workshop_ids   = [name_to_id[n] for n in WORKSHOP_CUSTOMERS if n in name_to_id]
    individual_ids = [name_to_id[n] for n in individual_names if n in name_to_id]

    # Per-customer payment reliability (0=unreliable, 1=always pays promptly in
    # full). NOT stored in the DB -- purely an in-memory trait used to drive
    # continuous, realistic variation in payment behavior below, instead of
    # every workshop landing in one bucket and every individual in another.
    reliability_map = {}
    for cid in workshop_ids:
        # Workshops are the store's real credit-risk population -- wide spread.
        reliability_map[cid] = round(random.betavariate(2.5, 2.5), 3)
    for cid in individual_ids:
        # Individuals are mostly reliable cash customers, with a minority who
        # occasionally run a small tab and don't always pay it off.
        reliability_map[cid] = round(random.betavariate(8, 1.5), 3)

    return workshop_ids, individual_ids, reliability_map


def generate_purchases(store_id, prod_map, supplier_weights, target=300):
    print(f"  Generating {target} purchase bills...")
    prod_list = list(prod_map.values())
    suppliers, weights = zip(*supplier_weights)
    count = 0
    current = START_DATE
    span_days = (END_DATE - START_DATE).days

    for _ in range(target):
        offset = random.randint(0, span_days)
        if random.random() < 0.30:
            restock_start = date(2025, 8, 1)
            offset = (restock_start - START_DATE).days + random.randint(0, 45)
        pdate = START_DATE + timedelta(days=min(offset, span_days))

        supplier_id = random.choices(suppliers, weights=weights, k=1)[0]
        n_items = random.randint(3, 12)
        selected = random.sample(prod_list, min(n_items, len(prod_list)))

        items = []
        gross_subtotal = 0
        net_subtotal = 0
        for prod in selected:
            qty = random.randint(5, 40)
            unit_price = prod["cost_price"] * random.uniform(0.95, 1.05)
            disc_pct = random.choice([0, 0, 15, 20, 28, 35, 41, 48])
            net_price = unit_price * (1 - disc_pct/100)
            gross_subtotal += qty * unit_price
            net_subtotal += qty * net_price
            items.append({
                "product_id": prod["id"], "product_name": prod["name"],
                "quantity": qty, "unit_price": round(unit_price, 2),
                "discount_percent": disc_pct,
                "total": round(qty * net_price, 2),
            })

        discount_total = round(gross_subtotal - net_subtotal, 2)
        tax = round(net_subtotal * 0.13, 2)
        total = round(net_subtotal + tax, 2)

        pur = supabase.table("purchases").insert({
            "store_id": store_id, "supplier_id": supplier_id,
            "bill_number": f"BILL-{pdate.strftime('%Y%m')}-{count:04d}",
            "purchase_date": str(pdate),
            "subtotal": round(net_subtotal, 2), "discount_total": discount_total,
            "tax": tax, "total": total, "paid_amount": total, "status": "paid",
        }).execute().data[0]

        for item in items:
            item["purchase_id"] = pur["id"]
        batch_insert("purchase_items", items, size=50)
        count += 1

    print(f"  Created {count} purchase bills")


def generate_sales(store_id, prod_map, workshop_ids, individual_ids, reliability_map):
    print("  Generating sales invoices (this will take a while)...")
    prod_list = list(prod_map.values())
    sumo_products = [p for p in prod_list if p.get("is_sumo")]

    total_invoices = 0
    invoice_records = []
    current = START_DATE

    while current <= END_DATE:
        mult = day_multiplier(current)
        if mult == 0.0:
            current += timedelta(days=1); continue

        in_festival = mult >= 1.5
        daily_target = random.uniform(70000, 85000) * mult
        remaining = daily_target

        if workshop_ids and random.random() < 0.40:
            cust_id = random.choice(workshop_ids)
            bulk = random.uniform(20000, 90000) * max(1.0, mult * 0.5 + 0.5)
            remaining -= bulk
            _write_invoice(store_id, cust_id, current, bulk, prod_list, sumo_products, in_festival,
                            payment_method="credit", total_invoices_ref=[total_invoices], invoice_records=invoice_records)
            total_invoices += 1

        guard = 0
        while remaining > 3500 and guard < 4:
            ticket = random.uniform(5000, 24000)
            remaining -= ticket
            cust_id = random.choice(individual_ids) if random.random() < 0.55 and individual_ids else None

            # Less-reliable individuals occasionally run a small tab instead of
            # paying immediately -- this is what gives the individual population
            # a genuine (if thin) spread of credit exposure, instead of every
            # individual having an unconditional Rs 0 balance.
            payment_method = random.choices(["cash","esewa","khalti","card","bank_transfer"], weights=[0.45,0.2,0.15,0.1,0.1])[0]
            if cust_id is not None:
                unreliability = 1 - reliability_map.get(cust_id, 0.85)
                if random.random() < unreliability * 0.35:
                    payment_method = "credit"

            _write_invoice(store_id, cust_id, current, ticket, prod_list, sumo_products, in_festival,
                            payment_method=payment_method, total_invoices_ref=[total_invoices], invoice_records=invoice_records)
            total_invoices += 1
            guard += 1

        current += timedelta(days=1)

    print(f"  Created {total_invoices} invoices")
    return invoice_records


def _write_invoice(store_id, cust_id, inv_date, target_amount, prod_list, sumo_products, in_festival,
                    payment_method, total_invoices_ref, invoice_records):
    n_prods = random.choices([1,2,3,4,5], weights=[0.30,0.30,0.20,0.13,0.07])[0]
    pool = prod_list
    if in_festival and sumo_products and random.random() < 0.35:
        pool = sumo_products + prod_list
    selected = random.sample(pool, min(n_prods, len(pool)))

    items, subtotal = [], 0
    remaining_amt = target_amount
    for idx, prod in enumerate(selected):
        share = remaining_amt / (len(selected) - idx)
        qty = max(1, round(share / max(prod["selling_price"], 1)))
        qty = min(qty, 20)
        line_total = qty * prod["selling_price"]
        disc = round(line_total * random.choice([0,0,0,0.03,0.05]), 2)
        line_total = round(line_total - disc, 2)
        subtotal += line_total
        remaining_amt -= line_total
        items.append({
            "product_id": prod["id"], "product_name": prod["name"],
            "quantity": qty, "unit_price": prod["selling_price"],
            "discount": disc, "total": line_total,
            "cost_price_at_sale": prod["cost_price"],
        })

    status = "paid" if payment_method != "credit" else "unpaid"
    invnum = f"INV-{inv_date.strftime('%Y%m%d')}-{total_invoices_ref[0]:04d}"

    inv = supabase.table("invoices").insert({
        "store_id": store_id, "customer_id": cust_id,
        "invoice_number": invnum, "invoice_date": str(inv_date),
        "subtotal": round(subtotal, 2), "discount": 0, "tax": 0, "delivery_charge": 0,
        "total": round(subtotal, 2),
        "paid_amount": round(subtotal, 2) if status == "paid" else 0,
        "payment_method": payment_method, "status": status,
    }).execute().data[0]

    for item in items:
        item["invoice_id"] = inv["id"]
    batch_insert("invoice_items", items, size=50)

    if cust_id:
        invoice_records.append({"invoice_id": inv["id"], "customer_id": cust_id,
                                 "total": subtotal, "status": status})


def generate_payments(store_id, invoice_records, reliability_map):
    print("  Generating payments for credit invoices...")
    by_customer = defaultdict(list)
    for r in invoice_records:
        if r["status"] == "unpaid":
            by_customer[r["customer_id"]].append(r)

    count = 0
    for cust_id, invs in by_customer.items():
        random.shuffle(invs)
        reliability = reliability_map.get(cust_id, 0.7)

        # Fraction of invoices this customer even attempts to pay down scales
        # with their own reliability, not a fixed range shared by everyone --
        # this is what lets some workshops end up nearly fully paid and others
        # end up mostly unpaid, instead of every workshop landing in the same
        # 60-90% band.
        pay_fraction = min(1.0, max(0.0, random.gauss(reliability, 0.12)))
        n_pay = int(len(invs) * pay_fraction)
        to_pay = invs[:n_pay]
        remaining_unpaid = 0

        for inv in to_pay:
            # How much of THIS invoice gets paid also scales with reliability,
            # with noise -- produces a real spread of partial payments instead
            # of a binary "pay it all or pay exactly half" choice.
            pay_ratio = min(1.0, max(0.0, random.gauss(reliability + 0.1, 0.2)))
            pay_amount = inv["total"] * pay_ratio
            pay_date_offset = random.randint(3, 25)
            pay_date = min(END_DATE, date.fromisoformat(str(START_DATE)) + timedelta(days=pay_date_offset))

            pay = supabase.table("payments").insert({
                "store_id": store_id, "customer_id": cust_id,
                "payment_date": str(pay_date), "amount": round(pay_amount, 2),
                "payment_method": random.choice(["cash","bank_transfer","esewa"]),
            }).execute().data[0]

            supabase.table("payment_allocations").insert({
                "payment_id": pay["id"], "invoice_id": inv["invoice_id"], "amount": round(pay_amount, 2),
            }).execute()

            new_paid = pay_amount
            new_status = "paid" if new_paid >= inv["total"] - 1 else "partial"
            supabase.table("invoices").update({
                "paid_amount": round(new_paid, 2), "status": new_status,
            }).eq("id", inv["invoice_id"]).execute()

            if new_status != "paid":
                remaining_unpaid += (inv["total"] - new_paid)
            count += 1

        for inv in invs[n_pay:]:
            remaining_unpaid += inv["total"]

        supabase.table("customers").update({"balance": round(remaining_unpaid, 2)}).eq("id", cust_id).execute()

    print(f"  Created {count} payments")


def generate_expenses(store_id):
    print("  Generating monthly expenses...")
    count = 0
    d = date(START_DATE.year, START_DATE.month, 1)
    while d <= END_DATE:
        for cat, low, high in [
            ("Rent", 25000, 25000), ("Salary", 45000, 45000),
            ("Electricity", 3500, 6500), ("Water", 500, 900),
            ("Transport", 2000, 4500), ("Telephone", 900, 1800),
            ("Marketing", 0, 3000), ("Maintenance", 500, 4000),
            ("Miscellaneous", 1000, 3500),
        ]:
            amount = random.randint(low, high) if low != high else low
            day = random.randint(1, 5) if cat in ("Rent","Salary") else random.randint(1, 27)
            try:
                exp_date = date(d.year, d.month, day)
            except ValueError:
                exp_date = date(d.year, d.month, 1)
            supabase.table("expenses").insert({
                "store_id": store_id, "category": cat, "amount": amount,
                "description": f"{cat} - {exp_date.strftime('%B %Y')}",
                "expense_date": str(exp_date),
            }).execute()
            count += 1
        d = date(d.year + (1 if d.month == 12 else 0), 1 if d.month == 12 else d.month + 1, 1)
    print(f"  Created {count} expense records")


def main():
    print("=" * 60)
    print("RetailSense Nepal - Data Generator v2")
    print("Bijeta Auto Parts - Auto Parts Retail Store")
    print("=" * 60)

    print("\n[1/7] Getting store ID...")
    store_id = get_store_id()
    print(f"      Store ID: {store_id}")

    products = build_products()

    print("\n[2/7] Setting up categories + products...")
    cat_map = setup_categories(store_id, products)
    prod_map = setup_products(store_id, products, cat_map)
    print(f"      {len(prod_map)} products ready")

    print("\n[3/7] Setting up suppliers...")
    supplier_weights = setup_suppliers(store_id)
    print(f"      {len(supplier_weights)} suppliers ready")

    print("\n[4/7] Setting up customers...")
    workshop_ids, individual_ids, reliability_map = setup_customers(store_id)
    print(f"      {len(workshop_ids)} workshops + {len(individual_ids)} individuals ready")

    print("\n[5/7] Generating purchases...")
    generate_purchases(store_id, prod_map, supplier_weights, target=300)

    print("\n[6/7] Generating sales invoices (Sep 2025 - Sep 2026)...")
    invoice_records = generate_sales(store_id, prod_map, workshop_ids, individual_ids, reliability_map)

    print("\n[7/7] Generating payments + expenses...")
    generate_payments(store_id, invoice_records, reliability_map)
    generate_expenses(store_id)

    print("\n" + "=" * 60)
    print("Data generation complete!")
    print(f"  Products:   {len(prod_map)}")
    print(f"  Suppliers:  {len(supplier_weights)}")
    print(f"  Customers:  {len(workshop_ids) + len(individual_ids)}")
    print(f"  Invoices:   ~{len(invoice_records)}")
    print("=" * 60)
    print("\nManual steps still needed (not scripted, by design):")
    print("  1. Staff & Roles: invite 2-3 fictional staff via the Manage Staff UI")
    print("     (real Supabase Auth users - can't be safely faked by a seed script)")
    print("  2. Scan Bill: upload one real bill photo through the actual Scan Bill")
    print("     flow so the AI-extraction review screen has real data to screenshot")


if __name__ == "__main__":
    main()
