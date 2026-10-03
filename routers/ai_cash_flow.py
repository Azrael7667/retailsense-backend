from fastapi import APIRouter, Depends

from middleware.auth_middleware import get_active_store_id, get_current_user
from services import ai_results as ai

router = APIRouter()


def _summarize(rs: list) -> dict:
    best_d, worst_d = max(rs, key=lambda r: r["net_cash_flow"]), min(rs, key=lambda r: r["net_cash_flow"])
    tr, te = sum(r["revenue"] for r in rs), sum(r["expenses"] for r in rs)
    return {"total_expected_revenue": round(tr, 2), "total_expected_expenses": round(te, 2), "total_expected_net": round(tr - te, 2),
            "avg_daily_revenue": round(tr / len(rs), 2), "best_day": best_d, "worst_day": worst_d}


@router.get("/cash-flow-forecast/status")
async def cash_flow_status(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    return ai.status("cash_flow", store_id)


@router.get("/cash-flow-forecast")
async def cash_flow_forecast(days: int = 30, user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    d = ai.require("cash_flow", store_id)
    daily = d["forecast_daily"]
    days = max(7, min(int(days), len(daily)))        # the notebook saved 90 days
    rows = daily[:days]
    return {"status": "success", "model": d["model"], "days": days, "trained_on": d["trained_on"], "data_through": d.get("data_through"),
            "metrics": d["metrics"], "meta": d.get("meta", {}), "summary": _summarize(rows), "forecast": rows}


@router.post("/cash-flow-forecast/train")
async def train_cash_flow(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    ai.require("cash_flow", store_id)
    return ai.TRAIN_MESSAGE
