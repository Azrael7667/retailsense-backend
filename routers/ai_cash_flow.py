import os
import json
import joblib
import pandas as pd
from datetime import date
from fastapi import APIRouter, HTTPException, BackgroundTasks
import sys

router    = APIRouter()
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")

# Hardcoded store for now — will be dynamic with real auth
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"


def load_model(store_id: str):
    rev_path  = os.path.join(MODEL_DIR, f"cash_flow_revenue_{store_id}.pkl")
    meta_path = os.path.join(MODEL_DIR, f"cash_flow_meta_{store_id}.json")
    if not os.path.exists(rev_path):
        return None, None
    rev_model = joblib.load(rev_path)
    with open(meta_path) as f:
        meta = json.load(f)
    return rev_model, meta


@router.get("/cash-flow-forecast")
async def cash_flow_forecast(days: int = 30):
    store_id        = STORE_ID
    rev_model, meta = load_model(store_id)

    if not rev_model:
        raise HTTPException(status_code=404, detail="Model not trained yet.")

    avg_expense = meta.get("avg_weekly_expense", 13324)
    weeks       = (days // 7) + 2

    future = rev_model.make_future_dataframe(periods=weeks, freq="W")
    future["is_monsoon"]  = future["ds"].dt.month.isin([6, 7, 8]).astype(int)
    future["is_festival"] = future["ds"].dt.month.isin([10, 11]).astype(int)
    future["is_q1"]       = future["ds"].dt.month.isin([1, 2, 3]).astype(int)

    rev_fc    = rev_model.predict(future)
    last_date = pd.Timestamp(meta.get("last_training_date", "2024-12-31"))

    rev_fut = rev_fc[rev_fc["ds"] > last_date][
        ["ds", "yhat", "yhat_lower", "yhat_upper"]
    ].head(weeks).copy()

    rev_fut["exp_yhat"]      = avg_expense
    rev_fut["yhat"]          = rev_fut["yhat"].clip(lower=0)
    rev_fut["yhat_lower"]    = rev_fut["yhat_lower"].clip(lower=0)
    rev_fut["yhat_upper"]    = rev_fut["yhat_upper"].clip(lower=0)
    rev_fut["net_cash_flow"] = rev_fut["yhat"] - rev_fut["exp_yhat"]

    daily_rows = []
    for _, row in rev_fut.iterrows():
        week_start = row["ds"]
        for d in range(7):
            day = week_start + pd.Timedelta(days=d)
            daily_rows.append({
                "date":          str(day.date()),
                "revenue":       round(float(row["yhat"]) / 7, 2),
                "revenue_lower": round(float(row["yhat_lower"]) / 7, 2),
                "revenue_upper": round(float(row["yhat_upper"]) / 7, 2),
                "expenses":      round(float(row["exp_yhat"]) / 7, 2),
                "net_cash_flow": round(float(row["net_cash_flow"]) / 7, 2),
            })

    forecast  = daily_rows[:days]
    total_rev = sum(r["revenue"] for r in forecast)
    total_exp = sum(r["expenses"] for r in forecast)
    total_net = sum(r["net_cash_flow"] for r in forecast)
    best_day  = max(forecast, key=lambda x: x["revenue"])
    worst_day = min(forecast, key=lambda x: x["revenue"])

    return {
        "status":     "success",
        "model":      "Prophet (weekly)",
        "days":       days,
        "trained_on": meta.get("trained_on"),
        "metrics":    meta.get("metrics", {}),
        "summary": {
            "total_expected_revenue":  round(total_rev, 2),
            "total_expected_expenses": round(total_exp, 2),
            "total_expected_net":      round(total_net, 2),
            "avg_daily_revenue":       round(total_rev / days, 2),
            "best_day":  best_day,
            "worst_day": worst_day,
        },
        "forecast": forecast,
    }


@router.post("/cash-flow-forecast/train")
async def train_cash_flow(background_tasks: BackgroundTasks):
    def run_training():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ml.training.cash_flow_model import train
        train(STORE_ID)
    background_tasks.add_task(run_training)
    return {"status": "training_started", "message": "Training started. Check back in 2 minutes."}


@router.get("/cash-flow-forecast/status")
async def cash_flow_status():
    _, meta = load_model(STORE_ID)
    if not meta:
        return {"trained": False}
    return {"trained": True, "trained_on": meta.get("trained_on"), "model": "Prophet (weekly)"}
