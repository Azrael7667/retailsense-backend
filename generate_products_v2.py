"""
Expands the Bijeta Auto Parts catalog past 3,000 entries by adding
brand-variant SKUs (OEM + common aftermarket brands) for existing parts.
Run from retailsense-backend/ with venv active: python3 generate_products_v2.py
"""
import random
from database import get_supabase_admin

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
random.seed(7)

supabase = get_supabase_admin()

# Pull existing catalog to avoid name collisions and to know current total value
existing = supabase.table("products").select("id,name,category_id,cost_price,stock_quantity") \
    .eq("store_id", STORE_ID).execute().data or []
existing_names = {p["name"] for p in existing}
existing_total = sum((p["cost_price"] or 0) * (p["stock_quantity"] or 0) for p in existing)
print(f"Existing: {len(existing)} products, total stock value Rs {existing_total:,.2f}")

cats = supabase.table("categories").select("id,name").eq("store_id", STORE_ID).execute().data or []
cat_ids = {c["name"]: c["id"] for c in cats}

VEHICLES = [
    "Tata Sumo", "Mahindra Scorpio S1", "Mahindra Scorpio S2", "Mahindra Scorpio S3",
    "Mahindra Scorpio S4", "Mahindra Scorpio S5", "Mahindra Scorpio S6",
    "Mahindra Scorpio S7", "Mahindra Scorpio S9", "Mahindra Scorpio S11",
    "Tata Ace", "Tata Ace Megha V10", "Tata Ace Megha V20", "Tata Yodha",
    "Mahindra Bolero Pickup", "Mahindra Bolero Camper",
]

PARTS = [
    ("Air Filter", "Filters", "Pcs", 150, 800, 10, 50),
    ("Oil Filter", "Filters", "Pcs", 150, 700, 10, 50),
    ("Fuel Filter", "Filters", "Pcs", 200, 900, 8, 40),
    ("Brake Pad", "Brake System", "Set", 800, 2500, 5, 30),
    ("Brake Shoe", "Brake System", "Set", 900, 2600, 5, 25),
    ("Clutch Plate", "Clutch & Transmission", "Pcs", 1500, 5000, 3, 15),
    ("Clutch Cable", "Clutch & Transmission", "Pcs", 300, 1200, 8, 35),
    ("Shock Absorber", "Suspension", "Pcs", 2000, 6000, 4, 20),
    ("Tie Rod End", "Suspension", "Pcs", 600, 2500, 5, 25),
    ("Ball Joint", "Suspension", "Pcs", 700, 2800, 5, 25),
    ("Wheel Bearing", "Suspension", "Pcs", 800, 3500, 5, 25),
    ("Water Pump", "Cooling System", "Pcs", 1500, 5000, 3, 15),
    ("Alternator", "Electrical", "Pcs", 4000, 18000, 2, 8),
    ("Starter Motor", "Electrical", "Pcs", 4500, 20000, 2, 8),
    ("Headlight Assembly", "Electrical", "Pcs", 800, 3500, 5, 20),
    ("Wiper Blade", "Electrical", "Pcs", 300, 1200, 8, 30),
    ("Side Mirror", "Body & Cabin", "Pcs", 500, 2500, 5, 20),
    ("Wheel Nut Set", "Wheels & Tyres", "Set", 300, 1200, 10, 40),
]

BRANDS = ["OEM Genuine", "Lucas TVS", "Bosch", "Rico", "Minda", "Endurance", "Local Aftermarket"]

def vehicle_code(v):
    return "".join(w[0] for w in v.split())

# Build candidate new products
candidates = []
for vehicle in VEHICLES:
    vcode = vehicle_code(vehicle)
    for pname, cat, unit, cmin, cmax, qmin, qmax in PARTS:
        for brand in BRANDS:
            name = f"{pname} - {vehicle} ({brand})"
            if name in existing_names:
                continue
            existing_names.add(name)
            cost = round(random.uniform(cmin, cmax), 2)
            selling = round(cost * random.uniform(1.15, 1.35), 2)
            qty = random.randint(qmin, qmax)
            sku = f"{vcode}-{pname[:3].upper()}-{brand[:2].upper()}-{random.randint(1000,9999)}"
            candidates.append({
                "store_id": STORE_ID,
                "category_id": cat_ids.get(cat),
                "name": name, "sku": sku, "unit": unit,
                "cost_price": cost, "selling_price": selling,
                "stock_quantity": qty, "reorder_level": max(2, int(qty * 0.15)),
                "is_active": True,
            })

random.shuffle(candidates)

needed = max(0, 3200 - len(existing))
new_batch = candidates[:needed]
print(f"Need {needed} more to pass 3,200. Generated {len(new_batch)} candidate rows.")

new_total = sum(p["cost_price"] * p["stock_quantity"] for p in new_batch)
TARGET = 40_000_000
remaining_budget = max(TARGET - existing_total, TARGET * 0.05)  # keep at least a small budget
scale = remaining_budget / new_total if new_total else 1
scale = max(0.15, min(scale, 2))  # keep prices sane, don't distort too much

for p in new_batch:
    p["cost_price"] = round(p["cost_price"] * scale, 2)
    p["selling_price"] = round(p["selling_price"] * scale, 2)

grand_total = existing_total + sum(p["cost_price"] * p["stock_quantity"] for p in new_batch)
print(f"Scale factor for new batch: {scale:.2f}x")
print(f"Projected grand total stock value: Rs {grand_total:,.2f}")

BATCH = 400
for i in range(0, len(new_batch), BATCH):
    chunk = new_batch[i:i+BATCH]
    supabase.table("products").insert(chunk).execute()
    print(f"  Inserted {i+len(chunk)}/{len(new_batch)}")

final_count = len(existing) + len(new_batch)
print(f"Done. Total products now: {final_count}")
