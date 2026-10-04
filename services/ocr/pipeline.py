import os
from io import BytesIO

from services.ocr.adapter import to_gemini_shape
from services.ocr.engine import load_image, run_ocr
from services.ocr.parser import parse_bill
from services.ocr.preprocess import preprocess
from services.ocr.validator import validate_bill


def extract_selfhosted(image_bytes: bytes):
    image = preprocess(load_image(BytesIO(image_bytes)), strip_lines=True)
    ocr = run_ocr(image, lang="eng+nep", psm=4)
    parsed = parse_bill(ocr["text"])
    val = validate_bill(parsed["fields"], parsed["items"], ocr["text"])
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
