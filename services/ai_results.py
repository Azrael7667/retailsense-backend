"""Serves AI results trained offline (Kaggle notebook) from ml/results/<store_id>/<name>_results.json.
Every shop only ever sees its own folder."""
import json
import os
import re
from typing import Any, Dict, Optional

from fastapi import HTTPException

from database import get_supabase_admin

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml", "results")

TRAIN_MESSAGE = {
    "status": "trained_offline",
    "message": "These helpers are trained offline in a notebook. The latest saved results were loaded.",
}

_cache: Dict[str, Any] = {}
_UUID = re.compile(r"^[0-9a-fA-F-]{8,64}$")          # a store id is a UUID; never build a file path from anything else


def _path(name: str, store_id: str) -> Optional[str]:
    sid = str(store_id)
    if not _UUID.match(sid):
        return None
    return os.path.join(RESULTS_DIR, sid, f"{name}_results.json")


def load(name: str, store_id: str) -> Optional[dict]:
    path = _path(name, store_id)
    if not path or not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    hit = _cache.get(path)
    if hit and hit[0] == mtime:                      # re-read automatically when the file is replaced
        return hit[1]
    with open(path) as f:
        data = json.load(f)
    _cache[path] = (mtime, data)
    return data


def require(name: str, store_id: str) -> dict:
    data = load(name, store_id)
    if data is None:
        raise HTTPException(status_code=404, detail="No AI results for this shop yet.")
    return data


def status(name: str, store_id: str) -> dict:
    data = load(name, store_id)
    if not data:
        return {"trained": False}
    return {"trained": True, "trained_on": data.get("trained_on"), "data_through": data.get("data_through"),
            "model": data.get("model"), "metrics": data.get("metrics", {})}


def customers(store_id: str) -> Dict[str, dict]:
    """Real names, phone numbers and live balances by id (the notebook only saw pseudonyms), for this shop only."""
    sb = get_supabase_admin()
    for cols in ("id, name, phone, balance, credit_limit", "id, name, balance, credit_limit"):   # second form if there is no phone column
        try:
            rows = sb.table("customers").select(cols).eq("store_id", store_id).range(0, 999).execute().data or []
            return {r["id"]: r for r in rows}
        except Exception:
            continue
    return {}


def invoice_customers(ids: list, store_id: str) -> Dict[str, dict]:
    """Invoice number and customer id for the given invoice ids, for this shop only."""
    out: Dict[str, dict] = {}
    try:
        sb = get_supabase_admin()
        for k in range(0, len(ids), 100):
            rows = (sb.table("invoices").select("id, invoice_number, customer_id").eq("store_id", store_id)
                    .in_("id", ids[k:k + 100]).execute().data or [])
            out.update({r["id"]: r for r in rows})
    except Exception:
        pass
    return out
