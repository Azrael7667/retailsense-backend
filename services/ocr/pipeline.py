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
