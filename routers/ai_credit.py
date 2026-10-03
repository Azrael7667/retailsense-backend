from fastapi import APIRouter, Depends, HTTPException

from middleware.auth_middleware import get_active_store_id, get_current_user
from services import ai_results as ai

router = APIRouter()


def _prepare(s: dict, names: dict) -> dict:
    out = dict(s)
    c = names.get(s["customer_id"])
    if c:                                            # real name and the balance as it is today
        bal, lim = float(c.get("balance") or 0), float(c.get("credit_limit") or 0)
        out.update(customer_name=c["name"], phone=c.get("phone"), current_balance=round(bal, 2), credit_limit=round(lim, 2),
                   balance_ratio=round(bal / lim, 3) if lim > 0 else 0.0)
    return out


@router.get("/credit-scoring/status")
async def credit_status(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    return ai.status("credit", store_id)


@router.get("/credit-scoring")
async def credit_scoring(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    d = ai.require("credit", store_id)
    names = ai.customers(store_id)
    return {**{k: v for k, v in d.items() if k != "scores"}, "scores": [_prepare(s, names) for s in d["scores"]]}


@router.get("/credit-scoring/{customer_id}")
async def credit_detail(customer_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    d = ai.require("credit", store_id)
    s = next((s for s in d["scores"] if s["customer_id"] == customer_id), None)
    if not s:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return {"status": "success", "score": _prepare(s, ai.customers(store_id))}


@router.post("/credit-scoring/train")
async def train_credit(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    ai.require("credit", store_id)
    return ai.TRAIN_MESSAGE
