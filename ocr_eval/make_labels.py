"""
Makes DRAFT label files for you to correct by hand against the paper bills.

    python ocr_eval/make_labels.py              # every bill in ocr_eval/bills/
    python ocr_eval/make_labels.py bill01       # just one

Drafts are written to ocr_eval/labels_draft/. They hold what the OCR read, which is NOT the truth.
Open each draft next to the paper bill, fix every wrong value, then MOVE the file to ocr_eval/labels/.
Only files in labels/ are scored by score.py. Scoring unchecked drafts would give a fake 100%.

Label keys: supplier_name, supplier_pan, bill_no, date_ad (YYYY-MM-DD), net_amount, and items with
description, part_no, unit, qty, rate, disc_pct, amount (= qty x rate, before discount).
Delete part_no / unit from a row when the bill does not print them. Add "page_note" for a multi-page bill.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))


def main():
    from services.ocr.pipeline import extract_selfhosted
    want = set(sys.argv[1:])
    out_dir = ROOT / "labels_draft"
    out_dir.mkdir(exist_ok=True)
    for p in sorted((ROOT / "bills").glob("*.jpg")):
        if want and p.stem not in want:
            continue
        data, val, ocr = extract_selfhosted(p.read_bytes())
        items = []
        for i in data["items"]:
            q, r = i.get("quantity"), i.get("unit_price")
            items.append({"description": i.get("name"), "part_no": i.get("part_number"), "unit": i.get("unit"),
                          "qty": q, "rate": r, "disc_pct": i.get("discount_percent") or 0,
                          "amount": round(float(q or 0) * float(r or 0), 2)})
        draft = {"_instructions": "OCR DRAFT. Correct every value from the paper bill, delete this key, move to labels/",
                 "supplier_name": data.get("supplier_name"), "supplier_pan": data.get("supplier_pan"),
                 "bill_no": data.get("bill_number"), "date_ad": data.get("bill_date"),
                 "net_amount": val["fields"].get("net_amount"), "items": items}
        (out_dir / f"{p.stem}.json").write_text(json.dumps(draft, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{p.stem}: {len(items)} rows -> labels_draft/{p.stem}.json")


if __name__ == "__main__":
    main()
