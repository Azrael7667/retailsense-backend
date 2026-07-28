import os, json
from fastapi import APIRouter, HTTPException, BackgroundTasks
import sys

router    = APIRouter()
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"

def load_data():
    meta_path = os.path.join(MODEL_DIR, f"inventory_meta_{STORE_ID}.json")
    fc_path   = os.path.join(MODEL_DIR, f"inventory_forecasts_{STORE_ID}.json")
    if not os.path.exists(meta_path):
        return None, None
    with open(meta_path) as f: meta = json.load(f)
    with open(fc_path) as f:   fc   = json.load(f)
    return meta, fc

@router.get("/inventory-demand")
async def inventory_demand(filter: str = "all"):
    meta, fc = load_data()
    if not meta:
        raise HTTPException(status_code=404, detail="Model not trained yet.")
    recs = fc.get("recommendations", [])
    if filter == "restock": recs = [r for r in recs if r["needs_restock"]]
    elif filter == "healthy": recs = [r for r in recs if not r["needs_restock"]]
    all_recs = fc.get("recommendations", [])
    return {
        "status": "success", "model": "LightGBM per product",
        "trained_on": meta.get("trained_on"),
        "summary": {
            "total_products": meta.get("n_products"),
            "needs_restock":  sum(1 for r in all_recs if r["needs_restock"]),
            "healthy_stock":  sum(1 for r in all_recs if not r["needs_restock"]),
        },
        "recommendations": recs,
    }

@router.post("/inventory-demand/train")
async def train_inventory(background_tasks: BackgroundTasks):
    def run():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ml.training.inventory_demand_model import train
        train(STORE_ID)
    background_tasks.add_task(run)
    return {"status": "training_started"}

@router.get("/inventory-demand/status")
async def inventory_status():
    meta, _ = load_data()
    if not meta: return {"trained": False}
    return {"trained": True, "trained_on": meta.get("trained_on")}
