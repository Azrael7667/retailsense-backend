from fastapi import APIRouter, Depends, HTTPException

from middleware.auth_middleware import get_active_store_id, get_current_user
from services import ai_results as ai

router = APIRouter()


def _prepare(p: dict, names: dict) -> dict:
    out = dict(p)
    c = names.get(p["customer_id"])
    if c:
        out["customer_name"] = c["name"]
        out["phone"] = c.get("phone")
    out["explanations"] = [
        f"{e['factor']}: {e['value']} ({'raises' if e['impact'] == 'negative' else 'lowers'} the risk)" if isinstance(e, dict) else e
        for e in p.get("explanations", [])]
    return out


@router.get("/customer-churn/status")
async def churn_status(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    return ai.status("churn", store_id)


def _page_summary(preds: list, model_summary: dict) -> dict:
    """The AI page labels three groups: stopped coming, slowing down, buying regularly.
    'Stopped coming' is a fact (no purchase for 60+ days). 'Slowing down' is the model's warning for customers who are still active."""
    lapsed = [p for p in preds if p["is_churned"]]
    active = [p for p in preds if not p["is_churned"]]
    slowing = [p for p in active if p["risk_level"] in ("high", "medium")]
    regular = len(active) - len(slowing)
    return {"total_customers": len(preds),
            "high_risk": len(lapsed), "medium_risk": len(slowing), "low_risk": regular,      # names the page reads
            "stopped_coming": len(lapsed), "slowing_down": len(slowing), "buying_regularly": regular,
            "model_risk": {k: model_summary.get(k) for k in ("high_risk", "medium_risk", "low_risk")}}


@router.get("/customer-churn")
async def customer_churn(risk: str = "all", user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    d = ai.require("churn", store_id)
    names = ai.customers(store_id)
    all_p = [_prepare(p, names) for p in d["predictions"]]
    preds = all_p if risk == "all" else [p for p in all_p if p["risk_level"] == risk]
    out = {k: v for k, v in d.items() if k not in ("predictions", "summary")}
    out["summary"] = _page_summary(all_p, d["summary"])
    out["predictions"] = preds
    return out


@router.get("/customer-churn/{customer_id}")
async def churn_detail(customer_id: str, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    d = ai.require("churn", store_id)
    p = next((p for p in d["predictions"] if p["customer_id"] == customer_id), None)
    if not p:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return {"status": "success", "prediction": _prepare(p, ai.customers(store_id))}


@router.post("/customer-churn/train")
async def train_churn(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    ai.require("churn", store_id)
    return ai.TRAIN_MESSAGE
