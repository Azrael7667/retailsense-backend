import logging
import os
import re
from io import BytesIO

import cv2
import numpy as np

from services.ocr.adapter import to_gemini_shape
from services.ocr.engine import load_image, run_ocr
from services.ocr.parser import _bill_no_v13, _supplier_pan_v13, parse_bill
from services.ocr.preprocess import preprocess
from services.ocr.table import (HS_CODE, _clean_name, name_score, read_table, rows_add_up,
                                totals_of, tidy_supplier)
from services.ocr.validator import validate_bill, words_amounts


def _name_quality(s) -> float:
    """Longer names made of real-looking words win; OCR crumbs (lowercase bits, symbols) cost."""
    toks = (s or "").split()
    good = sum(len(t) for t in toks if re.fullmatch(r"[A-Z0-9][A-Za-z0-9()\-/.,&+']*", t))
    bad = sum(1 for t in toks if not re.fullmatch(r"[A-Z0-9][A-Za-z0-9()\-/.,&+']*", t))
    return good - 4 * bad


def _pair_rows(base, other):
    """Row in `other` for each row in `base`: same amount, else same position when the counts agree."""
    by_amt = {}
    for o in other:
        if o.get("amount"):
            by_amt.setdefault(round(float(o["amount"]), 2), o)
    same_len = len(base) == len(other)
    return [by_amt.get(round(float(b.get("amount") or 0), 2)) or (other[k] if same_len else None)
            for k, b in enumerate(base)]


def _merge_text(base, other):
    """Fill name, part no and unit of `base` rows from the matching `other` rows where that reads better."""
    for b, o in zip(base, _pair_rows(base, other)):
        if not o:
            continue
        if _name_quality(o.get("description")) > _name_quality(b.get("description")):
            b["description"] = o["description"]
        if b.get("part_no") and HS_CODE.match(re.sub(r"\W", "", str(b["part_no"]))):
            b["part_no"] = None  # the HS code column, not a part number
        if o.get("part_no") and not HS_CODE.match(re.sub(r"\W", "", str(o["part_no"]))) and \
                (not b.get("part_no") or o.get("_from_columns")):
            b["part_no"] = o["part_no"]
        if o.get("unit") and not b.get("unit"):
            b["unit"] = o["unit"]
    for b in base:
        if b.get("part_no"):
            b["description"] = _clean_name(b.get("description") or "", b["part_no"])
    return base


def _clearly_better(new, old) -> bool:
    """No total to check against: take the column rows only if each one checks out on its own
    (qty x rate = amount, all three read) and the line reading has gaps."""
    if not new or any(i.get("needs_review") for i in new):
        return False
    old_gaps = not old or any(o.get("qty") is None or o.get("rate") is None for o in old)
    return old_gaps and len(new) >= len(old)


def extract_selfhosted(image_bytes: bytes):
    original = load_image(BytesIO(image_bytes))
    image = preprocess(original, strip_lines=True)
    ocr = run_ocr(image, lang="eng+nep", psm=4)
    parsed = parse_bill(ocr["text"])
    val = validate_bill(parsed["fields"], parsed["items"], ocr["text"])

    targets = totals_of(val["fields"])
    # the amount in words survives OCR better than digits: grand total / 1.13 is another check
    targets = sorted(set(targets + [round(w / 1.13, 2) for w in words_amounts(ocr["text"]) if w > 0]))

    # the column reader always runs: it reads part no, unit and names by position, and gives
    # a second opinion on the numbers when the line reading does not add up
    try:
        bgr = cv2.cvtColor(np.array(original), cv2.COLOR_RGB2BGR)
        table = read_table(bgr, targets)
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("column reader failed: %r", e)
        table = None

    fields = dict(parsed["fields"])
    top_text = (table or {}).get("top_text") or ""
    if top_text and not fields.get("bill_no"):  # number printed away from its label: retry on the word layout
        fields["bill_no"] = _bill_no_v13(top_text)
    if top_text and not fields.get("supplier_pan"):
        fields["supplier_pan"] = _supplier_pan_v13(top_text, None, fields.get("customer_pan"))
    names = [tidy_supplier(fields.get("supplier_name"))] + ((table or {}).get("supplier_names") or [])
    names = [n for n in names if n]
    if names:
        fields["supplier_name"] = max(names, key=name_score)

    note = None
    if table and not rows_add_up(val["items"], targets):
        if rows_add_up(table["items"], targets):
            note = "Item rows were read column by column (the line reading did not add up to the printed total)."
        elif _clearly_better(table["items"], val["items"]):
            note = ("Item rows were read column by column. The printed total could not be read, "
                    "so check the rows against the paper.")
    if note:
        items = _merge_text(table["items"], [dict(i) for i in parsed["items"]])
        val = validate_bill(fields, items, ocr["text"])
        val.setdefault("corrections", []).append(note)
    else:
        items = _merge_text([dict(i) for i in parsed["items"]], table["items"] if table else [])
        val = validate_bill(fields, items, ocr["text"])
    return to_gemini_shape(val, ocr), val, ocr


def _unreadable(val, ocr) -> bool:
    return (not val["items"]) or val["fields"].get("net_amount") is None or ocr["confidence"] < 35


def extract_with_engine(image_bytes: bytes, mime: str, gemini_fn):
    """
    OCR_ENGINE env var:
      selfhosted (default) - Tesseract pipeline only, no API key used
      hybrid               - self-hosted first, Gemini only if the bill is unreadable
      gemini               - the original paid flow
    """
    mode = os.getenv("OCR_ENGINE", "selfhosted").lower()
    if mode == "gemini":
        return gemini_fn(image_bytes, mime)
    try:
        data, val, ocr = extract_selfhosted(image_bytes)
        bad = _unreadable(val, ocr)
    except Exception:
        if mode != "hybrid":
            raise
        return gemini_fn(image_bytes, mime)
    if bad and mode == "hybrid":
        return gemini_fn(image_bytes, mime)
    if bad:
        data["notes"] = "Self-hosted OCR could not read this bill reliably. Enter the details manually.\n" + data["notes"]
    return data


# --- hybrid v2 patch: cloud only when the rows do not add up to the printed total ---
import hashlib
import json
import logging
from pathlib import Path

_CACHE_DIR = Path(os.getenv("OCR_CACHE_DIR", Path(__file__).resolve().parents[2] / ".ocr_cache"))


def _needs_cloud(val, ocr):
    items = val.get("items") or []
    f = val.get("fields") or {}
    if not items or f.get("net_amount") is None or ocr.get("confidence", 100) < 35:
        return True
    if any(not it.get("qty") or not it.get("rate") for it in items):
        return True
    T = f.get("total_amount")
    if T:
        gross = sum(float(it.get("qty") or 0) * float(it.get("rate") or 0) for it in items)
        tol = 2.0 + sum(0.006 * float(it.get("qty") or 0) for it in items)
        if abs(gross - T) > tol:
            return True
    return False


def _cloud_cached(image_bytes, mime, gemini_fn):
    _CACHE_DIR.mkdir(exist_ok=True)
    p = _CACHE_DIR / (hashlib.sha256(image_bytes).hexdigest() + ".json")
    if p.exists():
        return json.loads(p.read_text())
    r = gemini_fn(image_bytes, mime)
    p.write_text(json.dumps(r, ensure_ascii=False, default=str))
    return r


def _cloud_matches(cloud, val):
    T = (val.get("fields") or {}).get("total_amount")
    items = cloud.get("items") or []
    gross = sum(float(i.get("quantity") or 0) * float(i.get("unit_price") or 0) for i in items)
    if not T:
        return None, gross, T
    tol = 2.0 + sum(0.006 * float(i.get("quantity") or 0) for i in items)
    return abs(gross - T) <= tol, gross, T


_extract_with_engine_v2_orig = extract_with_engine


def extract_with_engine(image_bytes, mime, gemini_fn):
    if os.getenv("OCR_ENGINE", "selfhosted").lower() != "hybrid":
        return _extract_with_engine_v2_orig(image_bytes, mime, gemini_fn)
    try:
        data, val, ocr = extract_selfhosted(image_bytes)
    except Exception:
        return _cloud_cached(image_bytes, mime, gemini_fn)
    if not _needs_cloud(val, ocr):
        return data
    try:
        cloud = _cloud_cached(image_bytes, mime, gemini_fn)
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("cloud OCR unavailable: %r", e)
        data["notes"] = ("The cloud reader was unavailable (quota or network). "
                         "This is the self-hosted result: check every row.\n" + (data.get("notes") or ""))
        return data
    ok, gross, T = _cloud_matches(cloud, val)
    if ok is False:
        msg = f"Cloud reader rows add up to Rs {gross:,.2f} but the printed total is Rs {T:,.2f}. Check the rows."
    elif ok:
        msg = "Cloud reader rows match the printed total."
    else:
        msg = ""
    cloud["notes"] = (msg + "\n" if msg else "") + (cloud.get("notes") or "")
    return cloud
# --- end hybrid v2 patch ---
