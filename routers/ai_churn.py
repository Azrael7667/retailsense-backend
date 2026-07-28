import os, json
from fastapi import APIRouter, HTTPException, BackgroundTasks
import sys

router    = APIRouter()
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"

def load_data():
    meta_path = os.path.join(MODEL_DIR, f"churn_meta_{STORE_ID}.json")
    pred_path = os.path.join(MODEL_DIR, f"churn_predictions_{STORE_ID}.json")
    if not os.path.exists(meta_path): return None, None
    with open(meta_path) as f: meta = json.load(f)
    with open(pred_path) as f: pred = json.load(f)
    return meta, pred

@router.get("/customer-churn")
async def customer_churn(risk: str = "all"):
    meta, pred = load_data()
    if not meta:
        raise HTTPException(status_code=404, detail="Model not trained yet.")
    preds = pred.get("predictions", [])
    if risk == "high":   preds = [p for p in preds if p["risk_level"] == "high"]
    elif risk == "medium": preds = [p for p in preds if p["risk_level"] == "medium"]
    elif risk == "low":  preds = [p for p in preds if p["risk_level"] == "low"]
    all_p = pred.get("predictions", [])
    return {
        "status": "success", "model": "LightGBM + SHAP",
        "trained_on": meta.get("trained_on"),
        "churn_definition": f"No purchase in {meta.get('churn_days', 60)} days",
        "metrics": meta.get("metrics", {}),
        "summary": {
            "total_customers": len(all_p),
            "high_risk":   sum(1 for p in all_p if p["risk_level"] == "high"),
            "medium_risk": sum(1 for p in all_p if p["risk_level"] == "medium"),
            "low_risk":    sum(1 for p in all_p if p["risk_level"] == "low"),
        },
        "predictions": preds,
    }

@router.get("/customer-churn/{customer_id}")
async def churn_detail(customer_id: str):
    meta, pred = load_data()
    if not meta: raise HTTPException(status_code=404, detail="Model not trained yet.")
    p = next((p for p in pred.get("predictions", []) if p["customer_id"] == customer_id), None)
    if not p: raise HTTPException(status_code=404, detail="Customer not found.")
    return {"status": "success", "prediction": p}

@router.post("/customer-churn/train")
async def train_churn(background_tasks: BackgroundTasks):
    def run():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ml.training.churn_model import train
        train(STORE_ID)
    background_tasks.add_task(run)
    return {"status": "training_started"}

@router.get("/customer-churn/status")
async def churn_status():
    meta, _ = load_data()
    if not meta: return {"trained": False}
    return {"trained": True, "trained_on": meta.get("trained_on"), "metrics": meta.get("metrics", {})}
