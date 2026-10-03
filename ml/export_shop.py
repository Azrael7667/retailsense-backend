"""Exports ONE shop's data as CSV files for the AI training. Run it from the backend folder with the backend's Python:

  python ml/export_shop.py --store-id <uuid> --out /some/folder

Customer names become labels (Customer 001 ...), contact details and free text are dropped, the shop id is replaced.
The export is checked against the database; a half-finished export stops with an error so nothing trains on it.
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.getcwd())
from database import get_supabase_admin  # noqa: E402

DROP = {"notes", "remarks", "delivery_address", "delivery_note", "created_by", "created_by_name",
        "image_url", "image_urls", "email", "phone", "address", "pan_number", "description", "barcode"}
STORE_TABLES = ("customers", "categories", "products", "invoices", "purchases", "expenses")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-id", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not re.match(r"^[0-9a-fA-F-]{8,64}$", a.store_id):
        sys.exit("that does not look like a store id")
    sid, out = a.store_id, a.out
    os.makedirs(out, exist_ok=True)
    sb = get_supabase_admin()

    def page(build):
        rows, i = [], 0
        while True:
            part = build().range(i * 1000, (i + 1) * 1000 - 1).execute().data or []
            rows += part
            if len(part) < 1000:
                return rows
            i += 1

    def by_store(table):
        return page(lambda: sb.table(table).select("*").eq("store_id", sid).order("id"))

    def chunked(table, fk, ids):
        rows, ids = [], list(ids)
        for k in range(0, len(ids), 100):
            c = ids[k:k + 100]
            rows += page(lambda: sb.table(table).select("*").in_(fk, c).order("id"))
        return rows

    data = {t: by_store(t) for t in STORE_TABLES}
    data["invoice_items"] = chunked("invoice_items", "invoice_id", [r["id"] for r in data["invoices"]])

    # completeness: every row of the shop must have arrived
    bad = []
    for t in STORE_TABLES:
        live = sb.table(t).select("id", count="exact").eq("store_id", sid).limit(1).execute().count
        if len(data[t]) != live:
            bad.append(f"{t}: exported {len(data[t])}, database has {live}")
    if bad:
        sys.exit("export incomplete, stopping: " + "; ".join(bad))

    labels = {r["id"]: f"Customer {n:03d}" for n, r in enumerate(sorted(data["customers"], key=lambda r: (r.get("created_at") or "", r["id"])), 1)}
    for r in data["customers"]:
        r["name"] = labels[r["id"]]

    for name, rows in data.items():
        rows = [{k: ("S1" if k == "store_id" else v) for k, v in r.items() if k not in DROP} for r in rows]
        cols = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        with open(os.path.join(out, name + ".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"exported {name:<14} {len(rows):>6} rows")


if __name__ == "__main__":
    main()
