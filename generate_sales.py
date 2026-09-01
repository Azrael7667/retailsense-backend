"""
Generates a full year of realistic customers + invoices + invoice_items
for Bijeta Auto Parts, with seasonality (Dashain/Tihar boost, monsoon dip)
and genuine churn signal (some customers active all year, some who
clearly stopped buying partway through).

Run from retailsense-backend/ with venv active: python3 generate_sales.py
"""
import random
from datetime import date, timedelta
from database import get_supabase_admin

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
random.seed(23)
supabase = get_supabase_admin()

TODAY = date.today()
START = TODAY - timedelta(days=365)

# ----------------------------------------------------------------
# 1. Customers
# ----------------------------------------------------------------
FIRST_NAMES = [
    "Ram", "Shyam", "Hari", "Gita", "Sita", "Maya", "Kamala", "Sunita", "Deepak",
    "Suresh", "Mahesh", "Bikash", "Prakash", "Rajesh", "Anita", "Sarita", "Radha",
    "Laxmi", "Nirmala", "Bishnu", "Krishna", "Gopal", "Rabin", "Sandip", "Pradeep",
    "Umesh", "Dinesh", "Kiran", "Prem", "Bimal", "Sabina", "Rita", "Manoj",
    "Bimala", "Yubraj", "Sagar", "Nabin", "Sujata", "Rekha", "Binod",
]
LAST_NAMES = [
    "Sharma", "Thapa", "Gurung", "Magar", "Rai", "Limbu", "Shrestha", "Maharjan",
    "Tamang", "Adhikari", "Regmi", "Khadka", "Basnet", "Bohara", "Subedi",
    "Pandey", "Poudel", "Karki", "Ale", "Chettri", "K.C.", "Bista", "Jha",
    "Yadav", "Mahato", "Bhattarai", "Acharya", "Neupane",
]

used_names = set()
def unique_name():
    while True:
        n = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        if n not in used_names:
            used_names.add(n)
            return n

N_CUSTOMERS = 180
customers = []
for i in range(N_CUSTOMERS):
    r = random.random()
    if r < 0.55:
        ctype = "regular"       # buys steadily all year
    elif r < 0.75:
        ctype = "churned"       # stopped buying 60-250 days ago
    else:
        ctype = "occasional"    # sporadic, started partway through year

    customers.append({
        "name": unique_name(),
        "phone": f"98{random.randint(10000000,49999999)}",
        "credit_limit": random.choice([0, 5000, 10000, 20000, 50000]),
        "balance": 0,
        "notes": None,
        "_type": ctype,  # local only, not inserted
    })

print(f"Creating {len(customers)} customers...")
insert_rows = [{k: v for k, v in c.items() if not k.startswith("_")} | {"store_id": STORE_ID} for c in customers]
created_customers = []
for i in range(0, len(insert_rows), 200):
    chunk = supabase.table("customers").insert(insert_rows[i:i+200]).execute().data
    created_customers.extend(chunk)

for c, row in zip(customers, created_customers):
    c["id"] = row["id"]

regulars   = [c for c in customers if c["_type"] == "regular"]
churned    = [c for c in customers if c["_type"] == "churned"]
occasional = [c for c in customers if c["_type"] == "occasional"]

# churn cutoff: each churned customer's last purchase is somewhere 60-250 days before TODAY
for c in churned:
    c["_last_active"] = TODAY - timedelta(days=random.randint(60, 250))
for c in occasional:
    c["_start_active"] = START + timedelta(days=random.randint(0, 300))

print(f"  {len(regulars)} regular, {len(churned)} churned, {len(occasional)} occasional")

# ----------------------------------------------------------------
# 2. Load products (paginate past 1000-row cap)
# ----------------------------------------------------------------
products = []
offset = 0
while True:
    chunk = supabase.table("products").select("id,name,selling_price") \
        .eq("store_id", STORE_ID).range(offset, offset + 999).execute().data or []
    products.extend(chunk)
    if len(chunk) < 1000:
        break
    offset += 1000
print(f"Loaded {len(products)} products for invoice line items.")

# ----------------------------------------------------------------
# 3. Seasonality helpers
# ----------------------------------------------------------------
def day_multiplier(d: date) -> float:
    mult = 1.0
    # Dashain/Tihar boost — roughly mid-Sept to early-Nov (approx BS Ashwin-Kartik)
    if (d.month == 9 and d.day >= 15) or d.month == 10 or (d.month == 11 and d.day <= 10):
        mult *= 1.6
    # Monsoon dip — June to August
    if d.month in (6, 7, 8):
        mult *= 0.75
    # Saturday (Nepali weekly off) — quieter
    if d.weekday() == 5:  # Python Monday=0 ... Saturday=5
        mult *= 0.6
    # Gentle year-over-year growth trend, ~18% end vs start
    days_in = (d - START).days
    growth = 1.0 + 0.18 * (days_in / 365)
    return mult * growth

def customer_active_on(c, d: date) -> bool:
    if c["_type"] == "regular":
        return True
    if c["_type"] == "churned":
        return d <= c["_last_active"]
    if c["_type"] == "occasional":
        return d >= c["_start_active"]
    return True

# ----------------------------------------------------------------
# 4. Generate invoices day by day
# ----------------------------------------------------------------
BASE_INVOICES_PER_DAY = 8
invoice_counter = 1
invoices_to_insert = []
items_to_insert = []

cur = START
while cur <= TODAY:
    mult = day_multiplier(cur)
    n_invoices = max(0, round(random.gauss(BASE_INVOICES_PER_DAY * mult, 2)))

    eligible = [c for c in customers if customer_active_on(c, cur)]

    for _ in range(n_invoices):
        # 20% walk-in (no customer on file)
        customer = random.choice(eligible) if (eligible and random.random() > 0.20) else None

        n_items = random.randint(1, 5)
        chosen_products = random.sample(products, min(n_items, len(products)))

        line_items = []
        subtotal = 0
        for p in chosen_products:
            qty = random.randint(1, 4)
            price = float(p["selling_price"]) if p["selling_price"] else random.uniform(300, 3000)
            discount = round(price * qty * random.choice([0, 0, 0, 0.02, 0.05]), 2)
            total = round(price * qty - discount, 2)
            subtotal += total
            line_items.append({
                "product_id": p["id"], "product_name": p["name"],
                "quantity": qty, "unit_price": price, "discount": discount, "total": total,
            })

        subtotal = round(subtotal, 2)
        is_credit = customer is not None and random.random() < 0.35
        status = "unpaid" if is_credit else "paid"
        paid_amount = 0 if is_credit else subtotal
        payment_method = "credit" if is_credit else random.choice(["cash", "cash", "cash", "esewa", "bank"])

        invoice_number = f"INV-{cur.year}-{invoice_counter:05d}"
        invoice_counter += 1

        invoices_to_insert.append({
            "store_id": STORE_ID,
            "customer_id": customer["id"] if customer else None,
            "invoice_number": invoice_number,
            "invoice_date": cur.isoformat(),
            "subtotal": subtotal, "discount": 0, "tax": 0, "total": subtotal,
            "paid_amount": paid_amount, "payment_method": payment_method,
            "status": status, "notes": "Synthetic demo data",
            "_items": line_items,  # attach for post-insert linking
        })

    cur += timedelta(days=1)

print(f"Prepared {len(invoices_to_insert)} invoices spanning {START} to {TODAY}.")

# ----------------------------------------------------------------
# 5. Insert invoices in batches, then their line items
# ----------------------------------------------------------------
BATCH = 300
created_count = 0
for i in range(0, len(invoices_to_insert), BATCH):
    batch = invoices_to_insert[i:i+BATCH]
    rows = [{k: v for k, v in inv.items() if k != "_items"} for inv in batch]
    created = supabase.table("invoices").insert(rows).execute().data

    item_rows = []
    for inv, created_inv in zip(batch, created):
        for li in inv["_items"]:
            item_rows.append({**li, "invoice_id": created_inv["id"]})

    for j in range(0, len(item_rows), 500):
        supabase.table("invoice_items").insert(item_rows[j:j+500]).execute()

    created_count += len(batch)
    print(f"  {created_count}/{len(invoices_to_insert)} invoices + items inserted...")

print("Done.")
