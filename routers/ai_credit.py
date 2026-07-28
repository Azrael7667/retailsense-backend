import os, json
from fastapi import APIRouter, HTTPException, BackgroundTasks
import sys

router    = APIRouter()
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"

def load_data():
    meta_path  = os.path.join(MODEL_DIR, f"credit_meta_{STORE_ID}.json")
    score_path = os.path.join(MODEL_DIR, f"credit_scores_{STORE_ID}.json")
    if not os.path.exists(meta_path): return None, None
    with open(meta_path) as f:  meta   = json.load(f)
    with open(score_path) as f: scores = json.load(f)
    return meta, scores

@router.get("/credit-scoring")
async def credit_scoring(grade: str = "all"):
    meta, data = load_data()
    if not meta:
        raise HTTPException(status_code=404, detail="Model not trained yet.")
    scores = data.get("scores", [])
    if grade != "all": scores = [s for s in scores if s["grade"] == grade.upper()]
    all_scores = data.get("scores", [])
    grade_summary = {}
    for s in all_scores:
        g = s["grade"]
        grade_summary[g] = grade_summary.get(g, 0) + 1
    return {
        "status": "success", "model": "LightGBM + SHAP",
        "trained_on": meta.get("trained_on"),
        "metrics": {"lgbm": meta.get("lgbm_metrics", {}), "lr_baseline": meta.get("lr_metrics", {})},
        "summary": {
            "total_customers": meta.get("n_customers"),
            "bad_credit":      meta.get("n_bad_credit"),
            "grade_breakdown": grade_summary,
        },
        "scores": scores,
    }

@router.get("/credit-scoring/{customer_id}")
async def credit_score_customer(customer_id: str):
    meta, data = load_data()
    if not meta: raise HTTPException(status_code=404, detail="Model not trained yet.")
    score = next((s for s in data.get("scores", []) if s["customer_id"] == customer_id), None)
    if not score: raise HTTPException(status_code=404, detail="Customer not found.")
    return {"status": "success", "credit_score": score}

@router.post("/credit-scoring/train")
async def train_credit(background_tasks: BackgroundTasks):
    def run():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from ml.training.credit_score_model import train
        train(STORE_ID)
    background_tasks.add_task(run)
    return {"status": "training_started"}

@router.get("/credit-scoring/status")
async def credit_status():
    meta, _ = load_data()
    if not meta: return {"trained": False}
    return {"trained": True, "trained_on": meta.get("trained_on")}
