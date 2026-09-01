"""
Creates suppliers + purchases + purchase_items from the 136 real Bijeta
Auto Parts supplier bills (Purchase_Report.xlsx / karobar export),
exploded into realistic line items against the product catalog.

Run from retailsense-backend/ with venv active: python3 generate_purchases.py
"""
import random
from datetime import date
from database import get_supabase_admin
from utils.nepali_date import parse_bs_string_to_ad

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
random.seed(11)
supabase = get_supabase_admin()

# Real bills: [bill_no, supplier_name, bs_date "DD/MM/YYYY", total, paid, balance]
BILLS = [["204", "SIPRADI TRADING PVT.LTD", "21/11/2082", 11657.98, 0.0, 11657.98], ["11227", "SATYADIP INTERNATIONAL PVT. LTD", "18/11/2082", 11139.72, 0.0, 11139.72], ["11225", "SATYADIP INTERNATIONAL PVT. LTD", "18/11/2082", 60125.54, 0.0, 60125.54], ["3046", "ZOYA TRADE CONCERN PVT. LTD", "18/11/2082", 7572.77, 0.0, 7572.77], ["1247", "ROYAL AUTO PARTS", "18/11/2082", 118672.6, 0.0, 118672.6], ["15892", "SDI AUTO MOBILES PVT. LTD", "16/11/2082", 13334.11, 0.0, 13334.11], ["1347", "Darsan Trade Link Pvt. Ltd", "15/11/2082", 31360.89, 0.0, 31360.89], ["11116", "SATYADIP INTERNATIONAL PVT. LTD", "15/11/2082", 60097.24, 0.0, 60097.24], ["1202", "PRIYADIP TRADERS", "15/11/2082", 3506.31, 0.0, 3506.31], ["3003", "ZOYA TRADE CONCERN PVT. LTD", "14/11/2082", 51610.09, 0.0, 51610.09], ["3004", "ZOYA TRADE CONCERN PVT. LTD", "14/11/2082", 22500.56, 0.0, 22500.56], ["11018", "SATYADIP INTERNATIONAL PVT. LTD", "14/11/2082", 22425.46, 0.0, 22425.46], ["80", "BAJRASATYA TRADERS", "13/11/2082", 15784.22, 0.0, 15784.22], ["1445", "BAJRASATYA TRADERS", "13/11/2082", 26130.35, 0.0, 26130.35], ["246", "Mahavir Auto Spares Pvt. Ltd.", "13/11/2082", 169013.65, 0.0, 169013.65], ["1733", "SIPRADI AUTO PARTS PVT.LTD", "13/11/2082", 466058.3, 0.0, 466058.3], ["616", "SATYADIP INTERNATIONAL PVT. LTD", "12/11/2082", 16555.11, 0.0, 16555.11], ["14992", "SDI AUTO MOBILES PVT. LTD", "11/11/2082", 22040.98, 0.0, 22040.98], ["14993", "SIPRADI AUTO PARTS PVT.LTD", "09/11/2082", 262995.34, 0.0, 262995.34], ["3728", "SIPRADI AUTO PARTS PVT.LTD", "09/11/2082", 24199.39, 0.0, 24199.39], ["3131", "LADALI INTERNATIONAL PVT. LTD.", "08/11/2082", 22826.31, 0.0, 22826.31], ["1712", "SIPRADI AUTO PARTS PVT.LTD", "08/11/2082", 285165.21, 0.0, 285165.21], ["1368", "GOOD AUTOMOBILE PVT.LTD", "07/11/2082", 34211.88, 0.0, 34211.88], ["986", "Auto Zone Traders", "06/11/2082", 21414.08, 0.0, 21414.08], ["7965", "Mahavir Auto Spares Pvt. Ltd.", "06/11/2082", 150652.32, 0.0, 150652.32], ["2895", "ZOYA TRADE CONCERN PVT. LTD", "04/11/2082", 3500.37, 0.0, 3500.37], ["1169", "ROYAL AUTO PARTS", "03/11/2082", 18803.2, 0.0, 18803.2], ["1340", "GOOD AUTOMOBILE PVT.LTD", "01/11/2082", 14712.6, 0.0, 14712.6], ["2553", "SIPRADI TRADING PVT.LTD", "29/10/2082", 32943.18, 0.0, 32943.18], ["7753", "Mahavir Auto Spares Pvt. Ltd.", "29/10/2082", 73175.03, 0.0, 73175.03], ["3362", "SIPRADI TRADING PVT.LTD", "29/10/2082", 23132.68, 0.0, 23132.68], ["3176", "BEST BUY AUTO PVT. LTD", "28/10/2082", 27553.92, 0.0, 27553.92], ["2926", "LADALI INTERNATIONAL PVT. LTD.", "27/10/2082", 9418.09, 0.0, 9418.09], ["1352", "BAJRASATYA TRADERS", "27/10/2082", 35585.85, 0.0, 35585.85], ["5387", "SIPRADI TRADING PVT.LTD", "27/10/2082", 17082.71, 0.0, 17082.71], ["2900", "LADALI INTERNATIONAL PVT. LTD.", "26/10/2082", 17531.76, 0.0, 17531.76], ["1626", "SIPRADI AUTO PARTS PVT.LTD", "24/10/2082", 17791.24, 0.0, 17791.24], ["2861", "LADALI INTERNATIONAL PVT. LTD.", "23/10/2082", 13051.71, 0.0, 13051.71], ["7449", "Mahavir Auto Spares Pvt. Ltd.", "22/10/2082", 6643.42, 0.0, 6643.42], ["2454", "SIPRADI TRADING PVT.LTD", "22/10/2082", 50693.61, 0.0, 50693.61], ["1305", "BAJRASATYA TRADERS", "21/10/2082", 42157.84, 0.0, 42157.84], ["3578", "SIPRADI TRADING PVT.LTD", "21/10/2082", 19646.21, 0.0, 19646.21], ["891", "MAHAKALI AUTOMOBILES PVT.LTD", "20/10/2082", 16204.2, 0.0, 16204.2], ["2814", "LADALI INTERNATIONAL PVT. LTD.", "20/10/2082", 2577.09, 0.0, 2577.09], ["3210", "BNH AUTO TECH PVT LTD", "20/10/2082", 21380.73, 0.0, 21380.73], ["433", "Unique Enterprises & Traders", "20/10/2082", 28001.4, 0.0, 28001.4], ["1109", "ROYAL AUTO PARTS", "19/10/2082", 28882.8, 0.0, 28882.8], ["883", "MAHAKALI AUTOMOBILES PVT.LTD", "19/10/2082", 16204.2, 0.0, 16204.2], ["2667", "Rita Automobiles Pvt. Ltd.", "18/10/2082", 37802.0, 0.0, 37802.0], ["1170", "GAUTAM BUDDHA AUTO SPARES PVT. LTD.", "18/10/2082", 8632.79, 0.0, 8632.79], ["3387", "SIPRADI AUTO PARTS PVT.LTD", "16/10/2082", 394568.55, 0.0, 394568.55], ["2780", "LADALI INTERNATIONAL PVT. LTD.", "16/10/2082", 20370.01, 0.0, 20370.01], ["1111", "Darsan Trade Link Pvt. Ltd", "16/10/2082", 6799.21, 0.0, 6799.21], ["1110", "Darsan Trade Link Pvt. Ltd", "16/10/2082", 94624.17, 0.0, 94624.17], ["1554", "SIPRADI AUTO PARTS PVT.LTD", "16/10/2082", 25212.25, 0.0, 25212.25], ["3374", "SIPRADI AUTO PARTS PVT.LTD", "16/10/2082", 187400.14, 0.0, 187400.14], ["2653", "ZOYA TRADE CONCERN PVT. LTD", "15/10/2082", 4810.41, 0.0, 4810.41], ["847", "Auto Zone Traders", "15/10/2082", 10356.07, 0.0, 10356.07], ["4113", "NEXT GENERATION AUTOMOTIVE PVT. LTD.", "14/10/2082", 150585.54, 0.0, 150585.54], ["7099", "Mahavir Auto Spares Pvt. Ltd.", "14/10/2082", 188934.51, 0.0, 188934.51], ["2623", "ZOYA TRADE CONCERN PVT. LTD", "13/10/2082", 19799.86, 0.0, 19799.86], ["1236", "BAJRASATYA TRADERS", "13/10/2082", 17249.81, 0.0, 17249.81], ["9625", "SATYADIP INTERNATIONAL PVT. LTD", "13/10/2082", 8703.35, 0.0, 8703.35], ["2698", "LADALI INTERNATIONAL PVT. LTD.", "12/10/2082", 52508.12, 0.0, 52508.12], ["2311", "SIPRADI TRADING PVT.LTD", "12/10/2082", 9903.01, 0.0, 9903.01], ["224", "BAJRASATYA TRADERS", "12/10/2082", 36837.89, 0.0, 36837.89], ["3063", "BNH AUTO TECH PVT LTD", "12/10/2082", 27913.26, 0.0, 27913.26], ["9515", "SATYADIP INTERNATIONAL PVT. LTD", "12/10/2082", 28742.59, 0.0, 28742.59], ["2372", "SIPRADI TRADING PVT.LTD", "11/10/2082", 31439.0, 0.0, 31439.0], ["1396", "BAJRASATYA TRADERS", "05/10/2082", 29180.89, 0.0, 29180.89], ["16", "BNH AUTO TECH PVT LTD", "09/08/2082", 192275.15, 0.0, 192275.15], ["15", "Darsan Trade Link Pvt. Ltd", "02/08/2082", 381889.72, 0.0, 381889.72], ["14", "Darsan Trade Link Pvt. Ltd", "02/08/2082", 35030.0, 0.0, 35030.0], ["53", "Mahavir Auto Spares Pvt. Ltd.", "24/04/2082", 41279.62, 0.0, 41279.62], ["58", "AUTO SPARES NEPAL PVT LTD", "19/04/2082", 2395.6, 0.0, 2395.6], ["112", "ROYAL AUTO PARTS", "19/04/2082", 71495.1, 0.0, 71495.1], ["82", "GAUTAM BUDDHA AUTO SPARES PVT. LTD.", "18/04/2082", 7242.74, 0.0, 7242.74], ["238", "Rita Automobiles Pvt. Ltd.", "18/04/2082", 112160.49, 0.0, 112160.49], ["103", "BAJRASATYA TRADERS", "15/04/2082", 6791.14, 0.0, 6791.14], ["227", "BEST BUY AUTO PVT. LTD", "15/04/2082", 51095.38, 0.0, 51095.38], ["182", "SIPRADI TRADING PVT.LTD", "14/04/2082", 3867.19, 0.0, 3867.19], ["118", "NEW KASAJU ENTERPRISES", "14/04/2082", 9839.2, 0.0, 9839.2], ["14", "Nepal Auto Parts Group Pvt. Ltd.", "14/04/2082", 12845.84, 0.0, 12845.84], ["62", "Auto Zone Traders", "14/04/2082", 4500.79, 0.0, 4500.79], ["63", "Darsan Trade Link Pvt. Ltd", "14/04/2082", 5281.62, 0.0, 5281.62], ["210", "ZOYA TRADE CONCERN PVT. LTD", "14/04/2082", 37693.46, 0.0, 37693.46], ["82", "ZOYA TRADE CONCERN PVT. LTD", "14/04/2082", 15108.1, 0.0, 15108.1], ["195", "ZOYA TRADE CONCERN PVT. LTD", "13/04/2082", 22500.56, 0.0, 22500.56], ["72", "KAPISH TRADE CONCERN", "13/04/2082", 10730.37, 0.0, 10730.37], ["563", "Mahavir Auto Spares Pvt. Ltd.", "13/04/2082", 47175.76, 0.0, 47175.76], ["194", "ZOYA TRADE CONCERN PVT. LTD", "13/04/2082", 8450.0, 0.0, 8450.0], ["53", "Darsan Trade Link Pvt. Ltd", "12/04/2082", 16963.56, 0.0, 16963.56], ["68", "BAJRASATYA TRADERS", "12/04/2082", 1596.0, 0.0, 1596.0], ["28", "GAUTAM BUDDHA AUTO SPARES PVT. LTD.", "12/04/2082", 12035.71, 0.0, 12035.71], ["173", "BNH AUTO TECH PVT LTD", "12/04/2082", 43177.3, 0.0, 43177.3], ["170", "BNH AUTO TECH PVT LTD", "12/04/2082", 12791.6, 0.0, 12791.6], ["1038", "SDI AUTO MOBILES PVT. LTD", "12/04/2082", 10971.71, 0.0, 10971.71], ["527", "Mahavir Auto Spares Pvt. Ltd.", "12/04/2082", 23000.0, 0.0, 23000.0], ["48", "Auto Zone Traders", "12/04/2082", 35666.28, 0.0, 35666.28], ["66", "BAJRASATYA TRADERS", "12/04/2082", 24449.96, 0.0, 24449.96], ["162", "BNH AUTO TECH PVT LTD", "11/04/2082", 119390.15, 0.0, 119390.15], ["150", "SIPRADI TRADING PVT.LTD", "11/04/2082", 1861.52, 0.0, 1861.52], ["128", "Rita Automobiles Pvt. Ltd.", "11/04/2082", 22057.8, 0.0, 22057.8], ["110", "SIPRADI AUTO PARTS PVT.LTD", "11/04/2082", 23256.0, 0.0, 23256.0], ["125", "SIPRADI TRADING PVT.LTD", "11/04/2082", 11426.33, 0.0, 11426.33], ["43", "PRIYADIP TRADERS", "10/04/2082", 4536.05, 0.0, 4536.05], ["412", "Mahavir Auto Spares Pvt. Ltd.", "10/04/2082", 20000.01, 0.0, 20000.01], ["117", "SIPRADI TRADING PVT.LTD", "09/04/2082", 11498.16, 0.0, 11498.16], ["90", "Rita Automobiles Pvt. Ltd.", "09/04/2082", 121045.89, 0.0, 121045.89], ["77", "Rita Automobiles Pvt. Ltd.", "08/04/2082", 74521.01, 0.0, 74521.01], ["11", "GAUTAM BUDDHA AUTO SPARES PVT. LTD.", "08/04/2082", 76865.94, 0.0, 76865.94], ["613", "SDI AUTO MOBILES PVT. LTD", "07/04/2082", 15795.59, 0.0, 15795.59], ["31", "LADALI INTERNATIONAL PVT. LTD.", "07/04/2082", 7127.95, 0.0, 7127.95], ["286", "SATYADIP INTERNATIONAL PVT. LTD", "07/04/2082", 73619.64, 0.0, 73619.64], ["24", "Darsan Trade Link Pvt. Ltd", "07/04/2082", 72622.84, 0.0, 72622.84], ["46", "SIPRADI AUTO PARTS PVT.LTD", "07/04/2082", 50901.97, 0.0, 50901.97], ["30", "SIPRADI AUTO PARTS PVT.LTD", "06/04/2082", 297529.92, 0.0, 297529.92], ["231", "SATYADIP INTERNATIONAL PVT. LTD", "06/04/2082", 48933.61, 0.0, 48933.61], ["29", "MANAKAMANA INTERNATIONAL", "06/04/2082", 55207.28, 0.0, 55207.28], ["1", "BAJRASATYA TRADERS", "05/04/2082", 45226.14, 0.0, 45226.14], ["12", "LADALI INTERNATIONAL PVT. LTD.", "05/04/2082", 124878.12, 0.0, 124878.12], ["11", "ROYAL AUTO PARTS", "05/04/2082", 13779.22, 0.0, 13779.22], ["5", "BAJRASATYA TRADERS", "05/04/2082", 9033.48, 0.0, 9033.48], ["55", "SIPRADI TRADING PVT.LTD", "05/04/2082", 144949.04, 0.0, 144949.04], ["56", "AUTO SPARES NEPAL PVT LTD", "04/04/2082", 43219.2, 0.0, 43219.2], ["10", "Rita Automobiles Pvt. Ltd.", "04/04/2082", 344396.88, 0.0, 344396.88], ["9", "Mahavir Auto Spares Pvt. Ltd.", "04/04/2082", 20649.17, 0.0, 20649.17], ["8", "Rita Automobiles Pvt. Ltd.", "02/04/2082", 321387.92, 0.0, 321387.92], ["7", "Mahavir Auto Spares Pvt. Ltd.", "02/04/2082", 17844.15, 0.0, 17844.15], ["6", "Mahavir Auto Spares Pvt. Ltd.", "02/04/2082", 141432.55, 0.0, 141432.55], ["5", "Nepal Auto Parts Group Pvt. Ltd.", "02/04/2082", 12194.96, 0.0, 12194.96], ["13", "SDI AUTO MOBILES PVT. LTD", "01/04/2082", 6364.79, 0.0, 6364.79], ["1", "Mahavir Auto Spares Pvt. Ltd.", "01/04/2082", 22999.97, 0.0, 22999.97], ["4", "Nepal Auto Parts Group Pvt. Ltd.", "01/04/2082", 10994.9, 0.0, 10994.9], ["2", "Auto Zone Traders", "01/04/2082", 28450.21, 0.0, 28450.21], ["3", "S.E. International Pvt. Ltd.", "01/04/2082", 38571.42, 0.0, 38571.42]]

# ----------------------------------------------------------------
# 1. Suppliers — create any that don't already exist
# ----------------------------------------------------------------
supplier_names = sorted({b[1] for b in BILLS})
existing_suppliers = supabase.table("suppliers").select("id,name").eq("store_id", STORE_ID).execute().data or []
sup_ids = {s["name"]: s["id"] for s in existing_suppliers}

print(f"{len(supplier_names)} unique suppliers in bill data, {len(sup_ids)} already exist.")
for name in supplier_names:
    if name in sup_ids:
        continue
    row = supabase.table("suppliers").insert({
        "store_id": STORE_ID, "name": name, "balance": 0,
    }).execute().data[0]
    sup_ids[name] = row["id"]
print(f"Suppliers ready: {len(sup_ids)} total.")

# ----------------------------------------------------------------
# 2. Load product catalog (paginate past Supabase's 1000-row cap)
# ----------------------------------------------------------------
products = []
offset = 0
while True:
    chunk = supabase.table("products").select("id,name,cost_price") \
        .eq("store_id", STORE_ID).range(offset, offset + 999).execute().data or []
    products.extend(chunk)
    if len(chunk) < 1000:
        break
    offset += 1000
print(f"Loaded {len(products)} products for line-item generation.")

def bs_to_ad(bs_date_str):
    """Convert 'DD/MM/YYYY' BS -> AD date object."""
    d, m, y = bs_date_str.split("/")
    reformatted = f"{y}-{m}-{d}"  # utils.nepali_date expects YYYY-MM-DD
    converted = parse_bs_string_to_ad(reformatted)
    return converted  # date object or None

# ----------------------------------------------------------------
# 3. For each real bill, generate line items whose total matches
#    the real bill total exactly, using random products/quantities.
# ----------------------------------------------------------------
skipped = 0
created = 0

for bill_no, supplier_name, bs_date, total, paid, balance in BILLS:
    ad_date = bs_to_ad(bs_date)
    if not ad_date:
        skipped += 1
        continue

    n_items = random.randint(2, 8) if total < 50000 else random.randint(4, 14)
    chosen = random.sample(products, min(n_items, len(products)))

    raw_items = []
    for p in chosen:
        qty = random.randint(1, 25)
        base_price = float(p["cost_price"]) if p["cost_price"] else random.uniform(200, 3000)
        jitter = random.uniform(0.85, 1.15)
        unit_price = round(base_price * jitter, 2)
        raw_items.append({"product": p, "quantity": qty, "unit_price": unit_price})

    raw_sum = sum(i["quantity"] * i["unit_price"] for i in raw_items)
    scale = (total / raw_sum) if raw_sum else 1

    line_items = []
    running = 0
    for i, item in enumerate(raw_items):
        adj_price = round(item["unit_price"] * scale, 2)
        line_total = round(item["quantity"] * adj_price, 2)
        running += line_total
        line_items.append({
            "product_id": item["product"]["id"],
            "product_name": item["product"]["name"],
            "quantity": item["quantity"],
            "unit_price": adj_price,
            "total": line_total,
        })
    # Fix rounding drift on the last line item so subtotal matches exactly
    drift = round(total - running, 2)
    if line_items:
        line_items[-1]["total"] = round(line_items[-1]["total"] + drift, 2)
        if line_items[-1]["quantity"]:
            line_items[-1]["unit_price"] = round(line_items[-1]["total"] / line_items[-1]["quantity"], 2)

    status = "paid" if paid >= total else ("partial" if paid > 0 else "unpaid")

    purchase = supabase.table("purchases").insert({
        "store_id": STORE_ID,
        "supplier_id": sup_ids[supplier_name],
        "bill_number": bill_no,
        "purchase_date": ad_date.isoformat(),
        "subtotal": total,
        "tax": 0,
        "total": total,
        "paid_amount": paid,
        "status": status,
        "notes": "Imported from karobar Purchase Report (real supplier bill, synthetic line items)",
    }).execute().data[0]

    for li in line_items:
        li["purchase_id"] = purchase["id"]
    supabase.table("purchase_items").insert(line_items).execute()

    created += 1
    if created % 20 == 0:
        print(f"  {created}/{len(BILLS)} purchases created...")

print(f"Done. Created {created} purchases, skipped {skipped} (date conversion failed).")
