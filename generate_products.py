"""
Generates a realistic auto-parts product catalog for Bijeta Auto Parts.
Run from retailsense-backend/ with venv active: python3 generate_products.py
"""
import random
from database import get_supabase_admin

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
random.seed(42)

supabase = get_supabase_admin()

# ----------------------------------------------------------------
# 1. Categories
# ----------------------------------------------------------------
CATEGORY_NAMES = [
    "Filters", "Brake System", "Clutch & Transmission", "Suspension",
    "Engine Parts", "Electrical", "Body & Cabin", "Cooling System",
    "Wheels & Tyres", "Fasteners & Consumables",
]

print("Creating categories...")
existing = supabase.table("categories").select("id,name").eq("store_id", STORE_ID).execute().data or []
existing_names = {c["name"] for c in existing}
cat_ids = {c["name"]: c["id"] for c in existing}

for name in CATEGORY_NAMES:
    if name in existing_names:
        continue
    row = supabase.table("categories").insert({
        "store_id": STORE_ID, "name": name, "is_system": False,
    }).execute().data[0]
    cat_ids[name] = row["id"]

print(f"  {len(cat_ids)} categories ready.")

# ----------------------------------------------------------------
# 2. Vehicle models — small commercial vehicles common in Nepal
# ----------------------------------------------------------------
VEHICLES = [
    "Tata Sumo", "Mahindra Scorpio S1", "Mahindra Scorpio S2", "Mahindra Scorpio S3",
    "Mahindra Scorpio S4", "Mahindra Scorpio S5", "Mahindra Scorpio S6",
    "Mahindra Scorpio S7", "Mahindra Scorpio S9", "Mahindra Scorpio S11",
    "Tata Ace", "Tata Ace Megha V10", "Tata Ace Megha V20", "Tata Yodha",
    "Mahindra Bolero Pickup", "Mahindra Bolero Camper",
]

# ----------------------------------------------------------------
# 3. Part catalog — (part_name, category, unit, cost_min, cost_max, qty_min, qty_max, has_side_variant)
# ----------------------------------------------------------------
PARTS = [
    ("Air Filter", "Filters", "Pcs", 150, 800, 10, 60, False),
    ("Oil Filter", "Filters", "Pcs", 150, 700, 10, 60, False),
    ("Fuel Filter", "Filters", "Pcs", 200, 900, 10, 50, False),
    ("Cabin Filter", "Filters", "Pcs", 250, 1000, 8, 40, False),
    ("Brake Pad", "Brake System", "Set", 800, 2500, 5, 40, True),
    ("Brake Shoe", "Brake System", "Set", 900, 2600, 5, 35, True),
    ("Brake Drum", "Brake System", "Pcs", 1800, 6000, 3, 20, True),
    ("Brake Disc", "Brake System", "Pcs", 2000, 6500, 3, 18, True),
    ("Brake Caliper", "Brake System", "Pcs", 3000, 9000, 2, 15, True),
    ("Master Cylinder", "Brake System", "Pcs", 1500, 4500, 3, 20, False),
    ("Wheel Cylinder", "Brake System", "Pcs", 800, 3000, 5, 25, True),
    ("Clutch Plate", "Clutch & Transmission", "Pcs", 1500, 5000, 3, 20, False),
    ("Clutch Cover", "Clutch & Transmission", "Pcs", 1800, 5500, 3, 18, False),
    ("Clutch Cable", "Clutch & Transmission", "Pcs", 300, 1200, 10, 50, False),
    ("Propeller Shaft", "Clutch & Transmission", "Pcs", 8000, 25000, 1, 8, False),
    ("Gear Lever Assembly", "Clutch & Transmission", "Pcs", 1200, 4000, 3, 20, False),
    ("Differential Oil Seal", "Clutch & Transmission", "Pcs", 300, 1200, 10, 50, False),
    ("Axle Shaft", "Clutch & Transmission", "Pcs", 5000, 15000, 2, 10, True),
    ("Shock Absorber", "Suspension", "Pcs", 2000, 6000, 4, 25, True),
    ("Leaf Spring", "Suspension", "Pcs", 3000, 9000, 2, 15, True),
    ("U-Bolt Kit", "Suspension", "Set", 500, 1800, 8, 40, False),
    ("Tie Rod End", "Suspension", "Pcs", 600, 2500, 5, 30, True),
    ("Ball Joint", "Suspension", "Pcs", 700, 2800, 5, 30, True),
    ("Kingpin Kit", "Suspension", "Set", 1500, 4500, 3, 20, False),
    ("Wheel Bearing", "Suspension", "Pcs", 800, 3500, 5, 30, True),
    ("Wheel Hub", "Suspension", "Pcs", 1500, 4500, 3, 20, True),
    ("Engine Mounting", "Engine Parts", "Pcs", 1200, 3500, 5, 25, False),
    ("Gasket Set", "Engine Parts", "Set", 500, 2500, 5, 40, False),
    ("Timing Belt", "Engine Parts", "Pcs", 800, 2500, 8, 40, False),
    ("Fan Belt", "Engine Parts", "Pcs", 300, 1200, 10, 50, False),
    ("Water Pump", "Cooling System", "Pcs", 1500, 5000, 3, 20, False),
    ("Radiator", "Cooling System", "Pcs", 4000, 15000, 2, 10, False),
    ("Radiator Hose", "Cooling System", "Pcs", 400, 1500, 8, 40, False),
    ("Alternator", "Electrical", "Pcs", 4000, 18000, 2, 12, False),
    ("Starter Motor", "Electrical", "Pcs", 4500, 20000, 2, 10, False),
    ("Battery", "Electrical", "Pcs", 5000, 15000, 3, 20, False),
    ("Headlight Assembly", "Electrical", "Pcs", 800, 3500, 5, 30, True),
    ("Tail Light Assembly", "Electrical", "Pcs", 700, 3000, 5, 30, True),
    ("Indicator Light", "Electrical", "Pcs", 300, 1200, 8, 40, True),
    ("Wiring Harness", "Electrical", "Pcs", 3000, 12000, 2, 10, False),
    ("Horn", "Electrical", "Pcs", 400, 1500, 5, 30, False),
    ("Fuse Box", "Electrical", "Pcs", 300, 1200, 10, 40, False),
    ("Wiper Blade", "Electrical", "Pcs", 300, 1200, 10, 40, False),
    ("Wiper Motor", "Electrical", "Pcs", 1200, 3500, 3, 20, False),
    ("Door Handle", "Body & Cabin", "Pcs", 500, 2000, 5, 30, True),
    ("Side Mirror", "Body & Cabin", "Pcs", 500, 2500, 5, 30, True),
    ("Bumper", "Body & Cabin", "Pcs", 3000, 9000, 2, 15, True),
    ("Front Grille", "Body & Cabin", "Pcs", 1500, 4500, 3, 20, False),
    ("Seat Cover Set", "Body & Cabin", "Set", 1000, 4000, 5, 25, False),
    ("Floor Mat Set", "Body & Cabin", "Set", 600, 2000, 8, 30, False),
    ("Mud Flap", "Body & Cabin", "Pcs", 300, 900, 10, 40, True),
    ("Tarpaulin Cover", "Body & Cabin", "Pcs", 2000, 6000, 3, 15, False),
    ("Wheel Rim", "Wheels & Tyres", "Pcs", 3500, 9000, 3, 20, False),
    ("Tyre", "Wheels & Tyres", "Pcs", 6000, 18000, 4, 30, False),
    ("Wheel Nut Set", "Wheels & Tyres", "Set", 300, 1200, 10, 50, False),
    ("Engine Oil", "Fasteners & Consumables", "Ltr", 400, 1200, 20, 100, False),
    ("Gear Oil", "Fasteners & Consumables", "Ltr", 400, 1200, 15, 80, False),
    ("Coolant", "Fasteners & Consumables", "Ltr", 300, 900, 15, 70, False),
    ("Brake Fluid", "Fasteners & Consumables", "Ltr", 250, 700, 15, 70, False),
    ("Grease", "Fasteners & Consumables", "Kg", 200, 600, 15, 70, False),
    ("Nut Bolt Assortment", "Fasteners & Consumables", "Set", 200, 800, 15, 60, False),
]

SIDES = ["Front", "Rear"]

# ----------------------------------------------------------------
# 4. Generate SKU-unique product rows, targeting ~3200 entries
#    and total stock value (qty * cost) near Rs 4,00,00,000
# ----------------------------------------------------------------
def vehicle_code(v):
    return "".join(w[0] for w in v.split())  # "Tata Sumo" -> "TS"

products = []
seen_names = set()

for vehicle in VEHICLES:
    vcode = vehicle_code(vehicle)
    for pname, cat, unit, cmin, cmax, qmin, qmax, has_side in PARTS:
        variants = SIDES if has_side else [None]
        for side in variants:
            full_name = f"{pname} - {vehicle}" + (f" ({side})" for side in [side] if side).__next__() if side else f"{pname} - {vehicle}"
            if full_name in seen_names:
                continue
            seen_names.add(full_name)

            cost = round(random.uniform(cmin, cmax), 2)
            markup = random.uniform(1.15, 1.35)  # 15-35% margin
            selling = round(cost * markup, 2)
            qty = random.randint(qmin, qmax)
            sku = f"{vcode}-{pname[:3].upper()}-{random.randint(1000,9999)}"

            products.append({
                "store_id": STORE_ID,
                "category_id": cat_ids[cat],
                "name": full_name,
                "sku": sku,
                "unit": unit,
                "cost_price": cost,
                "selling_price": selling,
                "stock_quantity": qty,
                "reorder_level": max(2, int(qty * 0.15)),
                "is_active": True,
            })

# A batch of universal (non-vehicle-specific) consumables, sold across all models
UNIVERSAL_PARTS = [p for p in PARTS if p[1] == "Fasteners & Consumables"]
for pname, cat, unit, cmin, cmax, qmin, qmax, has_side in UNIVERSAL_PARTS:
    for i in range(6):  # a few different pack sizes/brands per consumable
        name = f"{pname} - Universal (Pack {i+1})"
        if name in seen_names:
            continue
        seen_names.add(name)
        cost = round(random.uniform(cmin, cmax), 2)
        selling = round(cost * random.uniform(1.15, 1.35), 2)
        qty = random.randint(qmin, qmax)
        sku = f"UNI-{pname[:3].upper()}-{random.randint(1000,9999)}"
        products.append({
            "store_id": STORE_ID, "category_id": cat_ids[cat], "name": name,
            "sku": sku, "unit": unit, "cost_price": cost, "selling_price": selling,
            "stock_quantity": qty, "reorder_level": max(2, int(qty * 0.15)), "is_active": True,
        })

print(f"Generated {len(products)} product rows.")
total_stock_value = sum(p["cost_price"] * p["stock_quantity"] for p in products)
print(f"Total stock value (cost basis): Rs {total_stock_value:,.2f}")

# ----------------------------------------------------------------
# 5. Scale cost_price so total lands close to target Rs 4,00,00,000
# ----------------------------------------------------------------
TARGET_VALUE = 40_000_000
scale = TARGET_VALUE / total_stock_value if total_stock_value else 1
if scale < 0.5 or scale > 3:
    print(f"  Scale factor {scale:.2f}x is large — capping to keep prices realistic.")
    scale = max(0.5, min(scale, 3))

for p in products:
    p["cost_price"] = round(p["cost_price"] * scale, 2)
    p["selling_price"] = round(p["selling_price"] * scale, 2)

final_value = sum(p["cost_price"] * p["stock_quantity"] for p in products)
print(f"After scaling ({scale:.2f}x): total stock value = Rs {final_value:,.2f}")

# ----------------------------------------------------------------
# 6. Insert in batches
# ----------------------------------------------------------------
BATCH = 400
for i in range(0, len(products), BATCH):
    chunk = products[i:i+BATCH]
    supabase.table("products").insert(chunk).execute()
    print(f"  Inserted {i+len(chunk)}/{len(products)}")

print("Done.")
