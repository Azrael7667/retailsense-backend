import re
from database import get_supabase_admin

PREFIXES = {
    "payment_in": "PI",
    "payment_out": "PO",
    "sales_return": "SR",
    "purchase_return": "PR",
    "quotation": "QT",
    "invoice": "INV",
    "purchase": "PUR",
}

_KNOWN = "INV|BILL|PUR|PI|PO|SR|PR|QT"


def next_doc_number(store_id: str, doc_type: str) -> str:
    """Atomically reserve the next per-store number, e.g. PI-0001."""
    res = get_supabase_admin().rpc(
        "next_doc_number", {"p_store_id": str(store_id), "p_doc_type": doc_type}
    ).execute()
    return f"{PREFIXES[doc_type]}-{int(res.data):04d}"


def creator_name(user) -> str | None:
    meta = getattr(user, "user_metadata", None) or {}
    return meta.get("full_name") or getattr(user, "email", None)


def short_doc_number(raw, doc_date=None) -> str:
    """Display-only PREFIX-YEAR-NUMBER form. Never write this back to the DB.
    Mirrors shortDocNumber() in the frontend (src/utils/docNumber.js)."""
    if not raw:
        return ""
    s = str(raw).strip()

    m = re.match(r"^INV-(\d{4})\d{4}-(\d+)$", s)
    if m:
        return f"INV-{m.group(1)}-{m.group(2)}"
    m = re.match(r"^BILL-(\d{4})\d{2}-(\d+)$", s)
    if m:
        return f"BILL-{m.group(1)}-{m.group(2)}"

    if re.match(rf"^({_KNOWN})-\d{{4}}-\d+$", s):
        return s

    m = re.match(rf"^({_KNOWN})-(\d+)$", s)
    if m:
        year = str(doc_date)[:4] if doc_date else ""
        if re.match(r"^\d{4}$", year):
            return f"{m.group(1)}-{year}-{m.group(2)}"
        return s

    return s
