from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
import pytesseract

from middleware.auth_middleware import get_current_user
from schemas.ocr import OCRResponse
from services.ocr.engine import load_image, run_ocr
from services.ocr.parser import parse_bill
from services.ocr.preprocess import preprocess as run_preprocess
from services.ocr.validator import validate_bill

router = APIRouter()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff"}
MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/extract", response_model=OCRResponse)
def extract(
    file: UploadFile = File(...),
    preprocess: bool = True,
    strip_lines: bool = True,
    lang: str = "eng+nep",
    psm: int = 4,
    parse: bool = True,
    user=Depends(get_current_user),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type: {file.content_type}")

    raw = file.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "Image too large (max 10 MB)")

    try:
        image = load_image(BytesIO(raw))
    except Exception:
        raise HTTPException(400, "Could not read image")

    if preprocess:
        try:
            image = run_preprocess(image, strip_lines=strip_lines)
        except Exception as e:
            raise HTTPException(500, f"Preprocessing failed: {e}")

    try:
        result = run_ocr(image, lang=lang, psm=psm)
    except pytesseract.TesseractNotFoundError:
        raise HTTPException(500, "Tesseract is not installed on the server")

    header = items = validation = None
    if parse:
        try:
            parsed = parse_bill(result["text"])
            val = validate_bill(parsed["fields"], parsed["items"], result["text"])
        except Exception as e:
            raise HTTPException(500, f"Parsing failed: {e}")
        header, items = val["fields"], val["items"]
        validation = {k: val[k] for k in ("status", "checks", "corrections", "needs_review")}

    return OCRResponse(**result, preprocessed=preprocess,
                       header=header, items=items, validation=validation)
