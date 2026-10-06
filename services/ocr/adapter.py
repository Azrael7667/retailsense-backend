import re
from typing import Any, Dict, List, Optional


def _pct(it: Dict) -> float:
    if it.get("disc_pct"):
        return float(it["disc_pct"])
    amt, net = it.get("amount"), it.get("net_amount")
    if amt and net is not None and 0 < amt - net < amt:
        return round((amt - net) / amt * 100, 2)
    return 0.0


def _clean_name(n):
    """Drop short lowercase OCR junk at the start or end of a supplier name."""
    if not n:
        return n
    toks = n.split()
    while len(toks) > 2 and toks[-1].islower() and len(toks[-1]) <= 4:
        toks.pop()
    while len(toks) > 2 and toks[0].islower() and len(toks[0]) <= 4:
        toks.pop(0)
    return " ".join(toks)


def _friendly(s: str) -> Optional[str]:
    """A validator check in words a shop owner can act on; None for checks that are internal only."""
    m = re.match(r"items_sum_matches_(?:taxable|total): rows sum ([0-9.]+), (?:taxable|total) ([0-9.]+)", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return (f"Item amounts add up to Rs {a:,.2f} but the bill says Rs {b:,.2f} "
                f"(difference Rs {abs(b - a):,.2f}). An item may be missing or misread.")
    simple = {
        "amount_in_words": "The amount in words could not be read, so the total is not double-checked.",
        "supplier_pan": "Supplier PAN was not read. Enter it from the paper.",
        "bill_no_present": "Bill number was not read. Enter it from the paper.",
        "date_present": "Bill date was not read. Enter it from the paper.",
        "date_not_in_future": "The bill date was read as a future date. Check it against the paper.",
        "items_found": "No item rows could be read. Enter the items from the paper.",
        "vat_is_13_percent": "VAT on the bill is not 13% of the taxable amount. Check the VAT rate.",
        "taxable_plus_vat_equals_net": "Taxable amount plus VAT does not match the bill total. Check the totals.",
    }
    for key, text in simple.items():
        if s.startswith(key):
            return text
    return None  # taxable_consistent, ad_bs_years_consistent, item_recovery: internal cross-checks


def _friendly_correction(s: str) -> Optional[str]:
    m = re.match(r"net_amount missing -> ([0-9.]+) \(from amount in words\)", s)
    if m:
        return f"Bill total taken from the amount in words: Rs {float(m.group(1)):,.2f}."
    return s if s[:1].isupper() else None  # lower-case ones are internal repair logs


def _reasons(it: Dict) -> List[str]:
    why = []
    if it.get("repair_note"):
        why.append(it["repair_note"])
    if it.get("net_recovered"):
        why.append("amount recovered by subtraction")
    if it.get("qty") is None or it.get("rate") is None:
        why.append("qty/rate not read, enter from the bill")
    elif it.get("net_derived"):
        why.append("net derived from discount")
    return why


def to_gemini_shape(val: Dict[str, Any], ocr: Dict[str, Any]) -> Dict[str, Any]:
    """Map validated self-hosted OCR output to the dict extract_bill_data() returns."""
    f = val["fields"]
    items = []
    for it in val["items"]:
        why = _reasons(it)
        items.append({
            "name": it.get("description") or it.get("part_no") or "",
            "part_number": it.get("part_no"),
            "unit": it.get("unit"),
            "quantity": it.get("qty"),
            "unit_price": it.get("rate"),
            "discount_percent": _pct(it),
            "needs_review": bool(why) or bool(it.get("needs_review")),
            "review_reason": "; ".join(why) or None,
        })

    notes = [f"Read by self-hosted OCR (Tesseract), confidence {ocr['confidence']}%. "
             "Check every flagged item against the paper bill."]
    if f.get("net_amount") is not None:
        notes.append(f"Bill total printed on the paper: Rs {f['net_amount']:,.2f}. Compare it with the Total below.")
    notes.append("The bill number cannot be verified automatically: compare it with the paper.")
    for n in [_friendly(n) for n in val.get("needs_review", []) if not n.startswith("row ")] + \
            [_friendly_correction(c) for c in val.get("corrections", [])]:
        if n and n not in notes:
            notes.append(n)

    return {
        "supplier_name": _clean_name(f.get("supplier_name")),
        "supplier_address": None,
        "supplier_pan": f.get("supplier_pan"),
        "bill_number": f.get("bill_no"),
        "invoice_type": None,
        "bill_date": f.get("date_ad"),
        "bill_date_calendar": "AD",   # date_ad is already converted to AD
        "paper_box": None,
        "items": items,
        "notes": "\n".join(notes),
    }
