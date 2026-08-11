import os, sys, json
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, HTTPException

router    = APIRouter()
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")

training_status = { "is_training": False, "started_at": None, "completed_at": None, "results": {} }

def get_model_status():
    models = {
        "cashFlow":  f"cash_flow_meta_{STORE_ID}.json",
        "inventory": f"inventory_meta_{STORE_ID}.json",
        "churn":     f"churn_meta_{STORE_ID}.json",
        "trend":     f"sales_trend_meta_{STORE_ID}.json",
        "anomaly":   f"anomaly_meta_{STORE_ID}.json",
        "credit":    f"credit_meta_{STORE_ID}.json",
    }
    status = {}
    for key, filename in models.items():
        path = os.path.join(MODEL_DIR, filename)
        if os.path.exists(path):
            with open(path) as f:
                meta = json.load(f)
            status[key] = { "trained": True, "trained_on": meta.get("trained_on"), "model": meta.get("model") }
        else:
            status[key] = { "trained": False }
    return status

@router.get("/model-status")
async def model_status():
    status = get_model_status()
    return { "models": status, "trained_count": sum(1 for s in status.values() if s.get("trained")), "total": 6 }

@router.post("/train-all")
async def train_all(background_tasks: BackgroundTasks):
    if training_status["is_training"]:
        return {"status": "already_training"}
    def run_all():
        training_status["is_training"] = True
        training_status["started_at"]  = datetime.now().isoformat()
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        import importlib
        for key, mod_path in [
            ("cashFlow",  "ml.training.cash_flow_model"),
            ("inventory", "ml.training.inventory_demand_model"),
            ("churn",     "ml.training.churn_model"),
            ("trend",     "ml.training.sales_trend_model"),
            ("anomaly",   "ml.training.anomaly_model"),
            ("credit",    "ml.training.credit_score_model"),
        ]:
            try:
                mod = importlib.import_module(mod_path)
                mod.train(STORE_ID)
                training_status["results"][key] = "success"
            except Exception as e:
                training_status["results"][key] = f"failed: {e}"
        training_status["is_training"]  = False
        training_status["completed_at"] = datetime.now().isoformat()
    background_tasks.add_task(run_all)
    return {"status": "training_started", "message": "All 6 models training in background"}

@router.post("/train/{model_key}")
async def train_single(model_key: str, background_tasks: BackgroundTasks):
    model_map = {
        "cashFlow":  "ml.training.cash_flow_model",
        "inventory": "ml.training.inventory_demand_model",
        "churn":     "ml.training.churn_model",
        "trend":     "ml.training.sales_trend_model",
        "anomaly":   "ml.training.anomaly_model",
        "credit":    "ml.training.credit_score_model",
    }
    if model_key not in model_map:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_key}")
    def run():
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        import importlib
        mod = importlib.import_module(model_map[model_key])
        mod.train(STORE_ID)
    background_tasks.add_task(run)
    return {"status": "training_started", "model": model_key}

@router.get("/training-progress")
async def training_progress():
    return training_status
