"""
Recalculates customers.balance from actual unpaid/partial invoice totals,
since it was left at 0 when customers were generated and never updated
to reflect the credit-risk variance we applied to invoices afterward.
"""
from database import get_supabase_admin

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
supabase = get_supabase_admin()

customers = []
offset = 0
while True:
    chunk = supabase.table("customers").select("id").eq("store_id", STORE_ID) \
        .range(offset, offset + 999).execute().data or []
    customers.extend(chunk)
    if len(chunk) < 1000:
        break
    offset += 1000
print(f"Loaded {len(customers)} customers.")

invoices = []
offset = 0
while True:
    chunk = supabase.table("invoices").select("customer_id,total,paid_amount,status") \
        .eq("store_id", STORE_ID).not_.is_("customer_id", "null") \
        .range(offset, offset + 999).execute().data or []
    invoices.extend(chunk)
    if len(chunk) < 1000:
        break
    offset += 1000
print(f"Loaded {len(invoices)} customer invoices.")

owed = {}
for inv in invoices:
    cid = inv["customer_id"]
    total = float(inv["total"] or 0)
    paid = float(inv["paid_amount"] or 0)
    outstanding = max(0, total - paid)
    owed[cid] = owed.get(cid, 0) + outstanding

updated = 0
for c in customers:
    bal = round(owed.get(c["id"], 0), 2)
    supabase.table("customers").update({"balance": bal}).eq("id", c["id"]).execute()
    updated += 1
    if updated % 50 == 0:
        print(f"  {updated}/{len(customers)} updated...")

print(f"Done. {updated} customers synced. {sum(1 for v in owed.values() if v > 0)} have outstanding balance.")
