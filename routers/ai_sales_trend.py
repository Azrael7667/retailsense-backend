import os, json
from fastapi import APIRouter, HTTPException, BackgroundTasks
import sys

router    = APIRouter()
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"

def load_data():
    path = os.path.join(MODEL_DIR, f"sales_trend_meta_{STORE_ID}.json")
    if not os.path.exists(path): return None
    with open(path) as f: return json.load(f)

@router.get("/sales-trend")
async def sales_trend():
    meta = load_data()
    if not meta:
        raise HTTPException(status_code=404, detail="Model not trained yet.")
    insights = meta.get("insights", {})
    return {
        "status": "success", "model": "Prophet + Optuna",
        "trained_on": meta.get("trained_on"),
        "best_params": meta.get("best_params", {}),
        "insights": {
            "trend_direction":  insights.get("trend_direction"),
            "trend_percent":    insights.get("trend_percent"),
            "avg_weekly_sales": insights.get("avg_weekly_sales"),
            "best_week_ever":   insights.get("best_week_ever"),
            "worst_week_ever":  insights.get("worst_week_ever"),
            "best_month":       insights.get("best_month"),
            "worst_month":      insights.get("worst_month"),
        },
        "forecast_8w": insights.get("forecast_8w", []),
        "historical":  insights.get("historical", []),
    }

@router.post("/sales-trend/train")
async def train_trend(background_tasks: BackgroundTasks):
    def run():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ml.training.sales_trend_model import train
        train(STORE_ID)
    background_tasks.add_task(run)
    return {"status": "training_started"}

@router.get("/sales-trend/status")
async def trend_status():
    meta = load_data()
    if not meta: return {"trained": False}
    return {"trained": True, "trained_on": meta.get("trained_on")}
