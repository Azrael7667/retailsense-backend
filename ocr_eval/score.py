"""
Scores the self-hosted OCR against hand-checked labels, field by field.

    python ocr_eval/score.py                  # all bills
    python ocr_eval/score.py bill_03 bill_07  # some bills
    python ocr_eval/score.py -v               # also print every wrong field
    python ocr_eval/score.py --tag v12        # also save results/<tag>.json

Bills live in ocr_eval/bills/, labels in ocr_eval/labels/ (both git-ignored).
What is scored is the dict the review screen receives, so a field counts as
right only if the user would see the right value and not need to edit it.

Every field printed on the bill is one point:
  header:  supplier name, supplier PAN, bill number, date
  per row: name, part no, unit, qty, rate, discount %
A row the OCR missed loses all its points; a junk row the OCR added costs one
point each. "Field accuracy" is right / (fields on the bills + junk rows).
"""
import argparse
import difflib
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

TESS_DIR = r"C:\Program Files\Tesseract-OCR"
if os.name == "nt" and os.path.isdir(TESS_DIR) and TESS_DIR not in os.environ["PATH"]:
    os.environ["PATH"] = TESS_DIR + os.pathsep + os.environ["PATH"]

VAT = 0.13
HEADER = ["supplier_name", "supplier_pan", "bill_no", "date"]
ROW = ["name", "part_no", "unit", "qty", "rate", "disc"]


def _alnum(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def _eq(a, b, tol=0.011) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _text_ok(got, want, cutoff=0.9) -> bool:
    """Same text ignoring case, spaces and punctuation, allowing a stray character in a long name."""
    g, w = _alnum(got), _alnum(want)
    if not w:
        return not g
    return g == w or difflib.SequenceMatcher(None, g, w).ratio() >= cutoff


def _supplier_ok(got, want) -> bool:
    strip = lambda s: re.sub(r"(pvt|private|ltd|limited|co|company|\d{2}-?\d{2})", "", _alnum(s))
    return _text_ok(strip(got), strip(want), 0.85)


def _unit_ok(got, want) -> bool:
    norm = lambda u: {"pieces": "pcs", "piece": "pcs", "pc": "pcs", "nos": "no"}.get(_alnum(u), _alnum(u))
    return norm(got) == norm(want)


def _run(stem: str):
    from services.ocr.pipeline import extract_selfhosted
    data, val, ocr = extract_selfhosted((ROOT / "bills" / f"{stem}.jpg").read_bytes())
    return stem, data, val["fields"], ocr["confidence"]


def _match_rows(pred, gold):
    """
    Pair predicted rows with label rows. Numbers count most (a row with the right
    qty x rate is the right row even if its name is garbled), then name and part
    number, then position on the bill.
    """
    scores = []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(pred):
            s = 0.0
            amt = float(p.get("quantity") or 0) * float(p.get("unit_price") or 0)
            if g.get("amount") and abs(amt - g["amount"]) <= 1.0:
                s += 2
            if _eq(p.get("unit_price"), g["rate"]):
                s += 1
            s += difflib.SequenceMatcher(None, _alnum(p.get("name")), _alnum(g["description"])).ratio()
            if g.get("part_no") and _alnum(g["part_no"]) == _alnum(p.get("part_number")):
                s += 1
            s += 0.5 * (1 - abs(gi / max(1, len(gold)) - pi / max(1, len(pred))))
            scores.append((s, gi, pi))
    pairs, used = {}, set()
    for s, gi, pi in sorted(scores, reverse=True):
        if s < 1.0 or gi in pairs or pi in used:
            continue
        pairs[gi] = pi
        used.add(pi)
    return pairs


def score_bill(label, data, fields):
    gold, pred = label["items"], data["items"]
    pairs = _match_rows(pred, gold)

    head = {
        "supplier_name": (_supplier_ok(data.get("supplier_name"), label.get("supplier_name")),
                          data.get("supplier_name"), label.get("supplier_name")),
        "supplier_pan": (_alnum(data.get("supplier_pan")) == _alnum(label.get("supplier_pan")),
                         data.get("supplier_pan"), label.get("supplier_pan")),
        "bill_no": (_alnum(data.get("bill_number")) == _alnum(label.get("bill_no")),
                    data.get("bill_number"), label.get("bill_no")),
        "date": (data.get("bill_date") == label.get("date_ad"), data.get("bill_date"), label.get("date_ad")),
    }
    # a field the label leaves empty (illegible / not printed) is not scored
    head = {k: v for k, v in head.items() if v[2]}

    rows = []
    for gi, g in enumerate(gold):
        p = pred[pairs[gi]] if gi in pairs else None
        f = {}
        want = {"name": g["description"], "part_no": g.get("part_no"), "unit": g.get("unit"),
                "qty": g["qty"], "rate": g["rate"], "disc": g.get("disc_pct") or 0}
        for k, w in want.items():
            if k in ("part_no", "unit") and not w:
                continue  # not printed on this bill
            if p is None:
                f[k] = (False, None, w)
                continue
            got = {"name": p.get("name"), "part_no": p.get("part_number"), "unit": p.get("unit"),
                   "qty": p.get("quantity"), "rate": p.get("unit_price"),
                   "disc": p.get("discount_percent") or 0}[k]
            ok = {"name": lambda: _text_ok(got, w), "part_no": lambda: _alnum(got) == _alnum(w),
                  "unit": lambda: _unit_ok(got, w), "qty": lambda: _eq(got, w),
                  "rate": lambda: _eq(got, w), "disc": lambda: _eq(got, w)}[k]()
            f[k] = (ok, got, w)
        rows.append({"row": gi + 1, "found": p is not None, "fields": f,
                     "exact": p is not None and all(v[0] for v in f.values())})

    sub = sum(float(i.get("quantity") or 0) * float(i.get("unit_price") or 0)
              * (1 - float(i.get("discount_percent") or 0) / 100) for i in pred)
    shown_total = round(sub * (1 + VAT), 2)
    want_total = label.get("net_amount")
    extra = len(pred) - len(pairs)

    right = sum(v[0] for v in head.values()) + sum(v[0] for r in rows for v in r["fields"].values())
    total = len(head) + sum(len(r["fields"]) for r in rows) + extra
    return {
        "head": head, "rows": rows, "rows_extra": extra,
        "fields_right": right, "fields_total": total,
        # a page of a multi-page bill has no total of its own
        "total_ok": None if label.get("page_note") or want_total is None
                    else abs(shown_total - want_total) <= 1.0 + 0.03 * len(pred),
        "shown_total": shown_total, "want_total": want_total,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bills", nargs="*")
    ap.add_argument("--tag")
    ap.add_argument("-v", action="store_true", help="print every wrong field")
    args = ap.parse_args()

    labels = {}
    for p in sorted((ROOT / "labels").glob("*.json")):
        lab = json.loads(p.read_text(encoding="utf-8"))
        if lab.get("duplicate_of") or not lab.get("items"):
            continue
        if args.bills and p.stem not in args.bills:
            continue
        labels[p.stem] = lab

    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        runs = list(ex.map(_run, labels))

    per = {k: [0, 0] for k in HEADER + ROW}
    results, R, N, tot_ok, tot_n, rows_exact, rows_gold, extra = {}, 0, 0, 0, 0, 0, 0, 0
    print(f"{'bill':8} {'fields':>9} {'rows ok':>8}  total")
    for stem, data, fields, conf in runs:
        r = score_bill(labels[stem], data, fields)
        results[stem] = r
        R, N = R + r["fields_right"], N + r["fields_total"]
        rows_exact += sum(x["exact"] for x in r["rows"])
        rows_gold += len(r["rows"])
        extra += r["rows_extra"]
        if r["total_ok"] is not None:
            tot_ok, tot_n = tot_ok + r["total_ok"], tot_n + 1
        for k, v in r["head"].items():
            per[k][0] += v[0]
            per[k][1] += 1
        for row in r["rows"]:
            for k, v in row["fields"].items():
                per[k][0] += v[0]
                per[k][1] += 1
        flag = {True: "OK ", False: "BAD", None: "n/a"}[r["total_ok"]]
        print(f"{stem:8} {r['fields_right']:>4}/{r['fields_total']:<4} "
              f"{sum(x['exact'] for x in r['rows']):>3}/{len(r['rows']):<3}  {flag}")
        if args.v:
            for k, (ok, got, want) in r["head"].items():
                if not ok:
                    print(f"         {k}: got {got!r} want {want!r}")
            for row in r["rows"]:
                bad = {k: v for k, v in row["fields"].items() if not v[0]}
                if not row["found"]:
                    print(f"         row {row['row']}: not found")
                elif bad:
                    print(f"         row {row['row']}: " + "; ".join(f"{k} {v[1]!r} want {v[2]!r}" for k, v in bad.items()))
            if r["rows_extra"]:
                print(f"         {r['rows_extra']} junk row(s)")

    if not results:
        print("no labelled bills")
        return
    pct = lambda a, b: f"{100 * a / b:5.1f}%" if b else "  n/a"
    print(f"\nSUMMARY over {len(results)} bills")
    print(f"  FIELD ACCURACY         {pct(R, N)}  ({R}/{N} fields, junk rows count as wrong)")
    print(f"  rows with every field right {pct(rows_exact, rows_gold)}  ({rows_exact}/{rows_gold}), junk rows {extra}")
    print(f"  bill total right       {pct(tot_ok, tot_n)}  ({tot_ok}/{tot_n})")
    print("  per field:")
    for k in HEADER + ROW:
        print(f"    {k:14} {pct(*per[k])}  ({per[k][0]}/{per[k][1]})")

    if args.tag:
        out = ROOT / "results" / f"{args.tag}.json"
        out.write_text(json.dumps({"per_field": per, "fields": [R, N], "bills": results}, indent=1, default=str))
        print("saved", out.relative_to(ROOT.parent))


if __name__ == "__main__":
    main()
