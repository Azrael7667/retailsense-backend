import asyncio
import json
import difflib
import time
import uuid
from datetime import date, datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body
from pydantic import BaseModel

from database import get_supabase_admin
from middleware.auth_middleware import require_role, get_active_store_id
from config import get_settings
from utils.nepali_date import parse_bs_string_to_ad

from google import genai
from google.genai import types

router = APIRouter()

BUCKET = "purchase-bills"
GEMINI_MODEL = "gemini-3.6-flash"  # pinned, "flash-latest" was drifting/timing out as of Aug 2026 model transition
MATCH_THRESHOLD = 0.90  # raised from 0.72, 0.72 wrongly matched "Oil Filter" to "Air Filter" at 0.80
DEFAULT_VAT_PERCENT = 13  # Nepal's standard VAT rate, used unless the bill clearly shows something else

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


# ----------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------

EXTRACTION_PROMPT = """You are reading a photo of a supplier purchase bill/invoice for a retail shop in Nepal.
Extract the following as strict JSON, with no markdown formatting, no commentary, just the JSON object:

{
  "supplier_name": string or null,
  "supplier_address": string or null (the supplier's address as printed at the top of the bill),
  "supplier_pan": string or null (the supplier's PAN / VAT number as printed, digits only),
  "bill_number": string or null,
  "invoice_type": string or null (e.g. "Cash" or "Credit", as printed next to "Invoice Type"),
  "bill_date": string in YYYY-MM-DD format, or null if unreadable,
  "bill_date_calendar": either "AD" or "BS", which calendar system the date on the
    bill is actually written in. Nepali bills very often use the Bikram Sambat (BS)
    calendar, which runs roughly 56-57 years ahead of AD (e.g. BS 2081 corresponds
    to AD 2024-2025). If the bill shows a 4-digit year in the 2080s while the actual
    date is clearly recent, it is almost certainly BS, not AD. Look for the word
    "Miti" (a common Nepali label for date) as a strong signal the date is BS.
  "paper_box": [ymin, xmin, ymax, xmax], the bounding box of the paper bill itself
    inside the photo, as integers from 0 to 1000 relative to the full image (0,0 is
    the top-left corner of the photo, 1000,1000 the bottom-right). Include only the
    sheet of paper, not the table, folder or hands around it. Use null if you cannot
    tell where the paper is.
  "items": [
    {
      "name": string,
      "part_number": string or null (the supplier's part/SKU code for this item,
        often in a column labeled "PART NO.", "Item Code", or similar, do not
        confuse this with the line number),
      "unit": string or null (e.g. "Pcs", "Box", "Ltr", "Kg", read exactly what
        the bill shows, do not guess or default to a value if the column is blank),
      "quantity": number,
      "unit_price": number,
      "discount_percent": number or null (read from a column labeled "Disc %",
        "Disc.", "Discount", or similar, if present on this line. Report exactly
        what the column shows, do NOT subtract it from unit_price yourself; the
        application applies it during review/approval.)
    }
  ],
  "notes": string or null (anything unclear or worth flagging for human review)
}

Rules:
- unit_price is the price PER UNIT charged by the supplier (cost price), not the line total.
- If a line only shows a total and quantity, divide to get unit_price.
- If you cannot confidently read a field, use null rather than guessing.
- Numbers must be plain numbers, not strings, and not include currency symbols or "%" signs.
"""

def extract_bill_data(image_bytes: bytes, mime_type: str) -> dict:
    """
    Calls Gemini with a short retry loop, the free-tier -latest alias
    occasionally returns 503 UNAVAILABLE / 504 DEADLINE_EXCEEDED under load,
    and that's usually transient (a few seconds to a couple minutes), not a
    real failure.

    Runs synchronously/blocking by design, the route calls this via
    asyncio.to_thread so it doesn't freeze the event loop.
    """
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)

    last_error = None
    delays = [2, 5, 15]  # seconds between attempts, short backoff, 4 tries total

    for attempt, delay in enumerate([0] + delays):
        if delay:
            time.sleep(delay)
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    EXTRACTION_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    http_options=types.HttpOptions(timeout=60_000),  # 60s hard timeout per attempt (images need more headroom than text)
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            last_error = e
            is_retryable = any(x in str(e) for x in ["UNAVAILABLE", "503", "429", "504", "DEADLINE_EXCEEDED"])
            if not is_retryable:
                raise  # don't retry real errors (bad key, malformed request, etc.)

    raise last_error


def fuzzy_match(name: str, candidates: List[dict]) -> Optional[dict]:
    """
    candidates: list of {"id": ..., "name": ...}
    Returns the best match dict (with a similarity score attached) if above
    threshold, else None. Uses difflib (stdlib, no extra dependency) rather
    than a fuzzy-matching library.
    """
    best = None
    best_score = 0.0
    name_lower = name.strip().lower()
    for c in candidates:
        score = difflib.SequenceMatcher(None, name_lower, c["name"].strip().lower()).ratio()
        if score > best_score:
            best_score = score
            best = c
    if best and best_score >= MATCH_THRESHOLD:
        return {**best, "match_confidence": round(best_score, 2)}
    return None


def _clean_box(box) -> Optional[List[int]]:
    """Validates the AI's paper bounding box: [ymin, xmin, ymax, xmax] on a 0-1000 scale."""
    try:
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        ymin, xmin, ymax, xmax = [int(round(float(v))) for v in box]
    except (TypeError, ValueError):
        return None
    ymin, xmin = max(0, ymin), max(0, xmin)
    ymax, xmax = min(1000, ymax), min(1000, xmax)
    # must be a sensible size (at least 20% of each side)
    if ymax - ymin < 200 or xmax - xmin < 200:
        return None
    return [ymin, xmin, ymax, xmax]


def build_review_draft(extracted: dict, store_id: str, supabase) -> dict:
    """
    Takes raw Gemini output and enriches it with product/supplier matching,
    producing the structure the frontend review screen actually renders.
    """
    products = supabase.table("products") \
        .select("id, name") \
        .eq("store_id", store_id).eq("is_active", True) \
        .execute().data or []

    suppliers = supabase.table("suppliers") \
        .select("id, name") \
        .eq("store_id", store_id) \
        .execute().data or []

    items_out = []
    for item in extracted.get("items", []):
        raw_name = (item.get("name") or "").strip()
        match = fuzzy_match(raw_name, products) if raw_name else None
        items_out.append({
            "extracted_name": raw_name,
            "product_id": match["id"] if match else None,
            "product_name": match["name"] if match else raw_name,
            "is_new": match is None,
            "part_number": item.get("part_number") or None,
            "unit": item.get("unit") or None,  # extracted from bill; reviewer can still edit
            "quantity": item.get("quantity") or 0,
            "unit_price": item.get("unit_price") or 0,
            "discount_percent": item.get("discount_percent") or 0,  # now applied to totals at approve time
            "match_confidence": match["match_confidence"] if match else None,
        })

    supplier_name = (extracted.get("supplier_name") or "").strip()
    supplier_match = None
    if supplier_name:
        supplier_match = fuzzy_match(supplier_name, suppliers)

    # Convert BS dates to AD before this ever reaches the frontend or gets
    # saved as a real purchase_date. Postgres only understands AD dates,
    # and Gemini is instructed to flag when a bill uses the BS calendar
    # (very common on Nepali supplier bills).
    raw_date = extracted.get("bill_date")
    calendar = (extracted.get("bill_date_calendar") or "AD").upper()
    date_note = None
    bill_date_ad = raw_date

    if raw_date and calendar == "BS":
        converted = parse_bs_string_to_ad(raw_date)
        if converted:
            bill_date_ad = converted.isoformat()
            date_note = f"Date converted from BS {raw_date} to AD {bill_date_ad}. Verify before approving."
        else:
            bill_date_ad = None
            date_note = f"Bill showed BS date '{raw_date}' but it could not be converted. Please enter the date manually."

    notes = extracted.get("notes")
    if date_note:
        notes = f"{notes}\n{date_note}" if notes else date_note

    return {
        "supplier_name": supplier_name or None,
        "supplier_id": supplier_match["id"] if supplier_match else None,
        "supplier_address": (extracted.get("supplier_address") or "").strip() or None,
        "supplier_pan": (str(extracted.get("supplier_pan") or "")).strip() or None,
        "invoice_type": (extracted.get("invoice_type") or "").strip() or None,
        "bill_number": extracted.get("bill_number"),
        "bill_date": bill_date_ad,
        "bill_date_raw": raw_date,
        "bill_date_calendar": calendar,
        "paper_box": _clean_box(extracted.get("paper_box")),
        "items": items_out,
        "notes": notes,
        # VAT is bill-level, not per-item. Defaulted here; reviewer can edit
        # in case a bill is VAT-exempt or shows a different rate.
        "vat_percent": DEFAULT_VAT_PERCENT,
    }


def check_duplicate_bill_number(bill_number: Optional[str], store_id: str, supabase) -> None:
    """
    Raises ValueError if a purchase with this bill number already exists
    for this store. Called right after extraction, before the document is
    marked ready_for_review, so a duplicate never even reaches the review
    screen; it goes straight to 'failed' with a clear message.
    """
    if not bill_number or not bill_number.strip():
        return  # nothing to check, let it through, reviewer can catch it manually
    existing = supabase.table("purchases") \
        .select("id") \
        .eq("store_id", store_id) \
        .eq("bill_number", bill_number.strip()) \
        .limit(1) \
        .execute().data
    if existing:
        raise ValueError(
            f"A purchase with bill number '{bill_number.strip()}' has already been recorded for this store."
        )


def _net_price(unit_price: float, discount_percent: float) -> float:
    disc = discount_percent or 0
    return round((unit_price or 0) * (1 - disc / 100.0), 4)


# ----------------------------------------------------------------
# Upload + extract
# ----------------------------------------------------------------

@router.post("/purchase-bill")
async def upload_purchase_bill(
    file: UploadFile = File(...),
    current_user=Depends(require_role("owner", "accountant")),
    store_id: str = Depends(get_active_store_id),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, or WEBP images are supported right now")

    supabase = get_supabase_admin()

    image_bytes = await file.read()
    ext = file.content_type.split("/")[-1]
    image_path = f"{store_id}/{uuid.uuid4()}.{ext}"

    try:
        supabase.storage.from_(BUCKET).upload(
            image_path, image_bytes, {"content-type": file.content_type}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not upload image: {e}")

    row = supabase.table("pending_documents").insert({
        "store_id": store_id,
        "doc_type": "purchase_bill",
        "status": "processing",
        "image_path": image_path,
        "created_by": current_user["id"],
    }).execute().data[0]

    doc_id = row["id"]

    try:
        raw_extracted = await asyncio.to_thread(extract_bill_data, image_bytes, file.content_type)

        # Block duplicates immediately, before this ever reaches review.
        check_duplicate_bill_number(raw_extracted.get("bill_number"), store_id, supabase)

        draft = build_review_draft(raw_extracted, store_id, supabase)
        updated = supabase.table("pending_documents").update({
            "status": "ready_for_review",
            "extracted_data": draft,
        }).eq("id", doc_id).execute().data[0]
        return updated
    except Exception as e:
        supabase.table("pending_documents").update({
            "status": "failed",
            "error_message": str(e),
        }).eq("id", doc_id).execute()
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")


# ----------------------------------------------------------------
# List + read
# ----------------------------------------------------------------

@router.get("/")
async def list_pending_documents(
    doc_type: str = "purchase_bill",
    status: Optional[str] = None,
    current_user=Depends(require_role("owner", "accountant")),
    store_id: str = Depends(get_active_store_id),
):
    supabase = get_supabase_admin()
    q = supabase.table("pending_documents") \
        .select("*") \
        .eq("store_id", store_id) \
        .eq("doc_type", doc_type) \
        .order("created_at", desc=True)
    if status:
        q = q.eq("status", status)
    return q.execute().data


@router.get("/{doc_id}")
async def get_pending_document(
    doc_id: str,
    current_user=Depends(require_role("owner", "accountant")),
    store_id: str = Depends(get_active_store_id),
):
    supabase = get_supabase_admin()
    row = supabase.table("pending_documents").select("*") \
        .eq("id", doc_id).eq("store_id", store_id) \
        .single().execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Not found")

    signed = supabase.storage.from_(BUCKET).create_signed_url(row.data["image_path"], 600)
    image_url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url")

    return {**row.data, "image_url": image_url}


# ----------------------------------------------------------------
# Edit draft before approving
# ----------------------------------------------------------------

@router.patch("/{doc_id}")
async def update_pending_document(
    doc_id: str,
    extracted_data: Dict[str, Any] = Body(..., embed=True),
    current_user=Depends(require_role("owner", "accountant")),
    store_id: str = Depends(get_active_store_id),
):
    supabase = get_supabase_admin()
    existing = supabase.table("pending_documents").select("id, status") \
        .eq("id", doc_id).eq("store_id", store_id) \
        .single().execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Not found")
    if existing.data["status"] not in ("ready_for_review", "failed"):
        raise HTTPException(status_code=400, detail=f"Cannot edit a document with status '{existing.data['status']}'")

    updated = supabase.table("pending_documents").update({
        "extracted_data": extracted_data,
        "status": "ready_for_review",
        "error_message": None,
    }).eq("id", doc_id).execute().data[0]
    return updated


# ----------------------------------------------------------------
# Approve, creates the real purchase (+ new products) + stock update
# ----------------------------------------------------------------

@router.post("/{doc_id}/approve")
async def approve_pending_document(
    doc_id: str,
    current_user=Depends(require_role("owner", "accountant")),
    store_id: str = Depends(get_active_store_id),
):
    supabase = get_supabase_admin()

    doc = supabase.table("pending_documents").select("*") \
        .eq("id", doc_id).eq("store_id", store_id).single().execute()
    if not doc.data:
        raise HTTPException(status_code=404, detail="Not found")
    if doc.data["status"] != "ready_for_review":
        raise HTTPException(status_code=400, detail=f"Cannot approve a document with status '{doc.data['status']}'")

    draft = doc.data["extracted_data"] or {}
    items = draft.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items to approve")

    # Re-check for duplicates here too, the reviewer may have edited the
    # bill number since upload, or a race could've let two scans through.
    try:
        check_duplicate_bill_number(draft.get("bill_number"), store_id, supabase)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 1. Create any flagged-new products first. cost_price is the NET
    #    (post-discount) price; list_price keeps the gross reference price.
    resolved_items = []
    for item in items:
        product_id = item.get("product_id")
        qty = item.get("quantity") or 0
        gross_price = item.get("unit_price") or 0
        disc_pct = item.get("discount_percent") or 0
        net_price = _net_price(gross_price, disc_pct)

        if item.get("is_new") and not product_id:
            new_product = supabase.table("products").insert({
                "store_id": store_id,
                "name": item["product_name"],
                "sku": item.get("part_number") or None,
                "unit": item.get("unit") or "pcs",
                "cost_price": net_price,
                "list_price": gross_price,
                "selling_price": 0,
                "stock_quantity": 0,  # purchase insert below adds the real quantity
                "reorder_level": 5,
                "product_type": "fast",
                "is_active": True,
            }).execute().data[0]
            product_id = new_product["id"]
        resolved_items.append({**item, "product_id": product_id, "net_price": net_price, "gross_price": gross_price})

    # 1b. If a supplier name was extracted but didn't fuzzy-match an existing
    #     supplier, create a new supplier record, mirrors how new products
    #     are auto-created above. Without this, supplier_id stays null forever
    #     for any supplier that isn't already in the system.
    supplier_id = draft.get("supplier_id")
    supplier_name = (draft.get("supplier_name") or "").strip()
    if not supplier_id and supplier_name:
        new_supplier_row = {"store_id": store_id, "name": supplier_name}
        if (draft.get("supplier_address") or "").strip():
            new_supplier_row["address"] = draft["supplier_address"].strip()
        if (draft.get("supplier_pan") or "").strip():
            new_supplier_row["pan_number"] = draft["supplier_pan"].strip()
        new_supplier = supabase.table("suppliers").insert(new_supplier_row).execute().data[0]
        supplier_id = new_supplier["id"]

    # 2. Create the purchase + line items, subtotal/VAT computed on the
    #    NET (post-discount) amount, matching how the supplier bill itself
    #    computes its taxable amount.
    gross_subtotal = sum((i["quantity"] or 0) * i["gross_price"] for i in resolved_items)
    subtotal       = sum((i["quantity"] or 0) * i["net_price"] for i in resolved_items)
    discount_total = round(gross_subtotal - subtotal, 2)

    try:
        vat_percent = float(draft.get("vat_percent", DEFAULT_VAT_PERCENT) or 0)
    except (TypeError, ValueError):
        vat_percent = DEFAULT_VAT_PERCENT
    tax = round(subtotal * (vat_percent / 100.0), 2)
    total = round(subtotal + tax, 2)

    purchase = supabase.table("purchases").insert({
        "store_id": store_id,
        "supplier_id": supplier_id,
        "bill_number": draft.get("bill_number"),
        "purchase_date": draft.get("bill_date") or date.today().isoformat(),
        "subtotal": round(subtotal, 2),
        "discount_total": discount_total,
        "tax": tax,
        "total": total,
        "paid_amount": total,
        "status": "paid",
        "notes": draft.get("notes") or f"Created from scanned bill (supplier: {draft.get('supplier_name') or 'unknown'})",
    }).execute().data[0]

    supabase.table("purchase_items").insert([
        {
            "purchase_id": purchase["id"],
            "product_id": i["product_id"],
            "product_name": i["product_name"],
            "quantity": i["quantity"],
            "unit_price": i["gross_price"],
            "discount_percent": i.get("discount_percent") or 0,
            "total": round((i["quantity"] or 0) * i["net_price"], 2),
        }
        for i in resolved_items
    ]).execute()

    # 3. Add stock + roll cost price forward (net cost becomes the new
    #    cost_price; whatever it was before is kept in previous_cost_price
    #    purely for reference, doesn't affect stock valuation math).
    for i in resolved_items:
        prod = supabase.table("products").select("stock_quantity, cost_price").eq("id", i["product_id"]).single().execute()
        if prod.data:
            new_qty = prod.data["stock_quantity"] + i["quantity"]
            supabase.table("products").update({
                "stock_quantity": new_qty,
                "previous_cost_price": prod.data["cost_price"],
                "cost_price": i["net_price"],
                "list_price": i["gross_price"],
            }).eq("id", i["product_id"]).execute()

    # 4. Mark the pending document approved
    supabase.table("pending_documents").update({
        "status": "approved",
        "resulting_purchase_id": purchase["id"],
        "reviewed_by": current_user["id"],
        "reviewed_at": datetime.utcnow().isoformat(),
    }).eq("id", doc_id).execute()

    return {"message": "Purchase created", "purchase_id": purchase["id"]}


# ----------------------------------------------------------------
# Reject
# ----------------------------------------------------------------

@router.post("/{doc_id}/reject")
async def reject_pending_document(
    doc_id: str,
    current_user=Depends(require_role("owner", "accountant")),
    store_id: str = Depends(get_active_store_id),
):
    supabase = get_supabase_admin()
    existing = supabase.table("pending_documents").select("id, status") \
        .eq("id", doc_id).eq("store_id", store_id) \
        .single().execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Not found")

    supabase.table("pending_documents").update({
        "status": "rejected",
        "reviewed_by": current_user["id"],
        "reviewed_at": datetime.utcnow().isoformat(),
    }).eq("id", doc_id).execute()

    return {"message": "Document rejected"}
