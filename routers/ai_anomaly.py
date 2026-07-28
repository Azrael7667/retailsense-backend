import os, json
from fastapi import APIRouter, HTTPException, BackgroundTasks
import sys

router    = APIRouter()
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"

def load_data():
    meta_path = os.path.join(MODEL_DIR, f"anomaly_meta_{STORE_ID}.json")
    res_path  = os.path.join(MODEL_DIR, f"anomaly_results_{STORE_ID}.json")
    if not os.path.exists(meta_path): return None, None
    with open(meta_path) as f: meta = json.load(f)
    with open(res_path) as f:  res  = json.load(f)
    return meta, res

@router.get("/anomaly-detection")
async def anomaly_detection(only_anomalies: bool = True):
    meta, res = load_data()
    if not meta:
        raise HTTPException(status_code=404, detail="Model not trained yet.")
    results = res.get("results", [])
    if only_anomalies: results = [r for r in results if r["is_anomaly"]]
    return {
        "status": "success", "model": "Isolation Forest",
        "trained_on": meta.get("trained_on"),
        "summary": {
            "total_transactions": meta.get("n_transactions"),
            "anomalies_detected": meta.get("n_anomalies"),
            "anomaly_rate":       meta.get("anomaly_rate"),
        },
        "anomalies": results,
    }

@router.post("/anomaly-detection/train")
async def train_anomaly(background_tasks: BackgroundTasks):
    def run():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ml.training.anomaly_model import train
        train(STORE_ID)
    background_tasks.add_task(run)
    return {"status": "training_started"}

@router.get("/anomaly-detection/status")
async def anomaly_status():
    meta, _ = load_data()
    if not meta: return {"trained": False}
    return {"trained": True, "trained_on": meta.get("trained_on"), "n_anomalies": meta.get("n_anomalies")}
