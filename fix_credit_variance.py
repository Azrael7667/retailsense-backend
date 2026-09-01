"""
Adds realistic credit-risk variance to existing invoices by adjusting
payment_method/status/paid_amount per customer, so the credit scoring
model actually differentiates grades instead of scoring everyone A.

Run from retailsense-backend/ with venv active: python3 fix_credit_variance.py
"""
import random
from database import get_supabase_admin

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
random.seed(31)
supabase = get_supabase_admin()

# Load all customers
customers = []
offset = 0
while True:
    chunk = supabase.table("customers").select("id,name") \
        .eq("store_id", STORE_ID).range(offset, offset + 999).execute().data or []
    customers.extend(chunk)
    if len(chunk) < 1000:
        break
    offset += 1000
print(f"Loaded {len(customers)} customers.")

# Assign each customer a risk profile:
#   good (55%): pays fully, on credit rarely, always clears balance
#   medium (25%): credit sometimes, partial payments common
#   risky (20%): credit often, frequently leaves balance unpaid
risk_choices = (["good"] * 55) + (["medium"] * 25) + (["risky"] * 20)
customer_risk = {c["id"]: random.choice(risk_choices) for c in customers}

good_count   = sum(1 for r in customer_risk.values() if r == "good")
medium_count = sum(1 for r in customer_risk.values() if r == "medium")
risky_count  = sum(1 for r in customer_risk.values() if r == "risky")
print(f"Risk profiles: {good_count} good, {medium_count} medium, {risky_count} risky")

# Load all invoices that have a customer (walk-ins with null customer_id are skipped)
invoices = []
offset = 0
while True:
    chunk = supabase.table("invoices").select("id,customer_id,total,status,paid_amount,payment_method") \
        .eq("store_id", STORE_ID).not_.is_("customer_id", "null").range(offset, offset + 999).execute().data or []
    invoices.extend(chunk)
    if len(chunk) < 1000:
        break
    offset += 1000
print(f"Loaded {len(invoices)} customer invoices to adjust.")

updates = []
for inv in invoices:
    risk = customer_risk.get(inv["customer_id"], "good")
    total = float(inv["total"] or 0)

    if risk == "good":
        is_credit = random.random() < 0.15
        paid_ratio = 1.0 if not is_credit else random.choice([1.0, 1.0, 0.9])
    elif risk == "medium":
        is_credit = random.random() < 0.40
        paid_ratio = 1.0 if not is_credit else random.choice([1.0, 0.7, 0.5, 0.3])
    else:  # risky
        is_credit = random.random() < 0.65
        paid_ratio = 1.0 if not is_credit else random.choice([0.5, 0.3, 0.1, 0.0, 0.0])

    paid_amount = round(total * paid_ratio, 2)
    status = "paid" if paid_amount >= total else ("partial" if paid_amount > 0 else "unpaid")
    payment_method = "credit" if is_credit else random.choice(["cash", "cash", "cash", "esewa", "bank"])

    updates.append({
        "id": inv["id"],
        "status": status,
        "paid_amount": paid_amount,
        "payment_method": payment_method,
    })

print(f"Applying {len(updates)} updates...")
count = 0
for u in updates:
    supabase.table("invoices").update({
        "status": u["status"], "paid_amount": u["paid_amount"], "payment_method": u["payment_method"],
    }).eq("id", u["id"]).execute()
    count += 1
    if count % 300 == 0:
        print(f"  {count}/{len(updates)} updated...")

print("Done.")
