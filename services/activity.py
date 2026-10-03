"""Records who changed what, for the notification bell.
One middleware watches every successful create, update or delete request and writes one line to activity_feed.
It is best effort: a logging problem never breaks the real request."""
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from starlette.concurrency import run_in_threadpool

from database import get_supabase_admin
from services.activity_ctx import REQUEST_INFO

TABLE = "activity_feed"
KEEP_PER_SHOP = 300

# first word after /api/  ->  (database table, how the record is named in a sentence)
RESOURCES = {
    "invoices": ("invoices", "sales invoice"),
    "purchases": ("purchases", "purchase"),
    "products": ("products", "product"),
    "customers": ("customers", "customer"),
    "suppliers": ("suppliers", "supplier"),
    "expenses": ("expenses", "expense"),
    "payments": ("payments", "payment received"),
    "payments-out": ("payments_out", "payment made"),
    "payments_out": ("payments_out", "payment made"),
    "quotations": ("quotations", "quotation"),
    "sales-returns": ("sales_returns", "sales return"),
    "sales_returns": ("sales_returns", "sales return"),
    "purchase-returns": ("purchase_returns", "purchase return"),
    "purchase_returns": ("purchase_returns", "purchase return"),
    "reminders": ("reminders", "reminder"),
    "khata": ("khata_entries", "khata entry"),
    "categories": ("categories", "category"),
    "pending-documents": ("pending_documents", "scanned bill"),
}
SKIP = {"ai", "admin", "ocr", "platform-admin", "activity", "classification"}
LABEL_KEYS = ("invoice_number", "bill_number", "quotation_number", "return_number", "name", "full_name", "title", "category")


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def describe(method: str, path: str) -> Optional[Dict[str, Any]]:
    """Turn 'DELETE /api/invoices/123' into what happened. None means: do not log this request."""
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "api":
        return None
    res, rest = parts[1], parts[2:]
    if res in SKIP:
        return None

    if res == "auth":
        if method == "POST" and rest == ["invite-staff"]:
            return dict(entity="staff member", table=None, id=None, verb="invited", action="created")
        if len(rest) >= 2 and rest[0] == "staff":
            sid = rest[1]
            if method == "PATCH" and rest[2:] == ["permissions"]:
                return dict(entity="staff member", table="users", id=sid, verb="changed the permissions of", action="updated")
            if method == "PATCH" and rest[2:] == ["deactivate"]:
                return dict(entity="staff member", table="users", id=sid, verb="deactivated", action="updated")
            if method == "DELETE" and len(rest) == 2:
                return dict(entity="staff member", table="users", id=sid, verb="removed", action="deleted")
        return None

    if res not in RESOURCES:
        return None
    table, entity = RESOURCES[res]

    if res == "pending-documents":
        if method == "POST" and rest == ["purchase-bill"]:
            return dict(entity="bill", table=None, id=None, verb="scanned", action="created", phrase="scanned a bill")
        if method == "DELETE" and rest == ["failed"]:
            return dict(entity="scans", table=None, id=None, verb="cleared", action="deleted", phrase="cleared the failed scans")

    if method == "POST" and not rest:
        return dict(entity=entity, table=table, id=None, verb="added", action="created")
    if method == "POST" and len(rest) == 2:
        verbs = {"approve": ("approved", "approved"), "reject": ("rejected", "rejected"), "convert": ("converted", "updated")}
        if rest[1] in verbs:
            v, a = verbs[rest[1]]
            return dict(entity=entity, table=table, id=rest[0], verb=v, action=a)
        return None
    if method in ("PUT", "PATCH") and len(rest) == 1:
        return dict(entity=entity, table=table, id=rest[0], verb="updated", action="updated")
    if method == "PATCH" and len(rest) == 2:
        verb = {"status": "changed the status of", "done": "marked as done"}.get(rest[1])
        if verb:
            return dict(entity=entity, table=table, id=rest[0], verb=verb, action="updated")
        return None
    if method == "DELETE" and len(rest) == 1:
        return dict(entity=entity, table=table, id=rest[0], verb="deleted", action="deleted")
    return None


def _label_from(row: Any, table: Optional[str] = None) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    if table == "pending_documents":
        ex = row.get("extracted_data") or {}
        for k in ("bill_number", "supplier_name", "vendor_name"):
            if isinstance(ex, dict) and ex.get(k):
                return str(ex[k])[:80]
        return None
    for k in LABEL_KEYS:
        if row.get(k):
            return str(row[k])[:80]
    if row.get("amount") is not None:
        try:
            return f"Rs {float(row['amount']):,.0f}"
        except (TypeError, ValueError):
            return None
    return None


def _label_from_response(body: bytes, table: Optional[str]) -> Optional[str]:
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, list) and data:
        data = data[0]
    label = _label_from(data, table)
    if label or not isinstance(data, dict):
        return label
    for v in data.values():                    # e.g. {"invoice": {...}}
        if isinstance(v, dict):
            label = _label_from(v, table)
            if label:
                return label
    return None


def _prefetch(table: str, rid: str):
    """The record as it is BEFORE the change, so a deleted thing can still be named."""
    rows = get_supabase_admin().table(table).select("*").eq("id", rid).limit(1).execute().data or []
    return rows[0] if rows else None


_names: Dict[str, str] = {}
_counts: Dict[str, int] = {}


def _user_name(sb, user_id: str) -> str:
    if user_id in _names:
        return _names[user_id]
    try:
        rows = sb.table("users").select("full_name, email").eq("id", user_id).limit(1).execute().data or []
        name = (rows[0].get("full_name") or (rows[0].get("email") or "").split("@")[0]) if rows else ""
    except Exception:
        name = ""
    if name:
        _names[user_id] = name
    return name or "Someone"


def _token_user(scope) -> Optional[str]:
    """The user id (sub) from the bearer token. Only used if the route did not report the user itself."""
    try:
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        part = auth.split(" ", 1)[1].split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("sub")
    except Exception:
        return None


def _trim(sb, store_id: str):
    _counts[store_id] = _counts.get(store_id, 0) + 1
    if _counts[store_id] % 25:
        return
    old = sb.table(TABLE).select("id").eq("store_id", store_id).order("created_at", desc=True).range(KEEP_PER_SHOP, KEEP_PER_SHOP + 500).execute().data or []
    ids = [r["id"] for r in old]
    for k in range(0, len(ids), 100):
        sb.table(TABLE).delete().in_("id", ids[k:k + 100]).execute()


def _recent_duplicate(sb, store_id, user_id, entity, action, label) -> bool:
    """The database trigger may already have logged this change (when the page wrote straight to Supabase)."""
    since = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
    rows = (sb.table(TABLE).select("label").eq("store_id", store_id).eq("user_id", user_id).eq("entity", entity)
            .eq("action", action).gte("created_at", since).limit(5).execute().data or [])
    return any((not r.get("label")) or (not label) or r.get("label") == label for r in rows)


def _record(scope, desc, info, before, body: bytes):
    sb = get_supabase_admin()
    user_id = info.get("user_id") or _token_user(scope)
    if not user_id:
        return
    store_id = info.get("store_id")
    if not store_id:                           # older routes use the user's home shop
        rows = sb.table("users").select("store_id").eq("id", user_id).limit(1).execute().data or []
        store_id = rows[0]["store_id"] if rows else None
    if not store_id:
        return

    label = None
    if before and (desc["table"] == "users" or str(before.get("store_id")) == str(store_id)):
        label = _label_from(before, desc["table"])          # only use a name that belongs to this shop
    if not label and body:
        label = _label_from_response(body, desc["table"])

    if _recent_duplicate(sb, store_id, user_id, desc["entity"], desc["action"], label):
        return

    who = _user_name(sb, user_id)
    if desc.get("phrase"):
        summary = f"{who} {desc['phrase']}"
    elif label:
        summary = f"{who} {desc['verb']} {desc['entity']} {label}"
    else:
        summary = f"{who} {desc['verb']} {_article(desc['entity'])} {desc['entity']}"
    sb.table(TABLE).insert({"store_id": store_id, "user_id": user_id, "user_name": who, "action": desc["action"],
                            "entity": desc["entity"], "label": label, "summary": summary}).execute()
    _trim(sb, store_id)


class ActivityLogMiddleware:
    """Pure ASGI middleware (not BaseHTTPMiddleware) so the request holder is shared with the route code."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH", "DELETE"):
            return await self.app(scope, receive, send)
        desc = describe(scope["method"], scope.get("path", ""))
        if not desc:
            return await self.app(scope, receive, send)

        before = None
        if desc["id"] and desc["table"]:
            try:
                before = await run_in_threadpool(_prefetch, desc["table"], desc["id"])
            except Exception:
                before = None

        info: Dict[str, str] = {}
        token = REQUEST_INFO.set(info)
        state = {"status": 500, "json": False, "size": 0}
        chunks = []

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["json"] = b"json" in dict(message.get("headers") or []).get(b"content-type", b"")
            elif message["type"] == "http.response.body" and state["json"] and desc["action"] == "created" and state["size"] < 65536:
                part = message.get("body", b"")
                chunks.append(part)
                state["size"] += len(part)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            REQUEST_INFO.reset(token)

        if state["status"] < 400:
            try:
                await run_in_threadpool(_record, scope, desc, info, before, b"".join(chunks))
            except Exception:
                pass                              # logging must never break a request
