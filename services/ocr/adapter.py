from typing import Any, Dict, List


def _pct(it: Dict) -> float:
    if it.get("disc_pct"):
        return float(it["disc_pct"])
    amt, net = it.get("amount"), it.get("net_amount")
    if amt and net is not None and 0 < amt - net < amt:
        return round((amt - net) / amt * 100, 2)
    return 0.0


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
            "unit": None,
            "quantity": it.get("qty"),
            "unit_price": it.get("rate"),
            "discount_percent": _pct(it),
            "needs_review": bool(why) or bool(it.get("needs_review")),
            "review_reason": "; ".join(why) or None,
        })

    notes = [f"Read by self-hosted OCR (Tesseract), confidence {ocr['confidence']}%. "
             "Check every flagged item against the paper bill."]
    notes += [n for n in val.get("needs_review", []) if not n.startswith("row ")]
    notes += val.get("corrections", [])

    return {
        "supplier_name": f.get("supplier_name"),
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
