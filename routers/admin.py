import os, sys, json
from datetime import datetime, date, timedelta
from fastapi import APIRouter, BackgroundTasks, HTTPException
from database import get_supabase_admin

router    = APIRouter()
STORE_ID  = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml", "models_saved")

training_status = { "is_training": False, "started_at": None, "completed_at": None, "results": {} }

REORDER_LEVELS = {
    "fast":       5,
    "moderate":   3,
    "slow":       2,
    "dead_stock": 2,
}

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


@router.post("/classify-products")
async def classify_products():
    """
    Recalculate product_type (fast / moderate / slow / dead_stock) from the
    last 90 days of sales. Grace period: products added < 30 days ago are
    left untouched. Products with zero sales and zero stock are left
    untouched (sold out, not dead).
    """
    supabase = get_supabase_admin()

    cutoff_90    = (date.today() - timedelta(days=90)).isoformat()
    grace_cutoff = date.today() - timedelta(days=30)

    products = supabase.table("products") \
        .select("id, product_type, stock_quantity, created_at") \
        .eq("store_id", STORE_ID) \
        .eq("is_active", True) \
        .execute().data

    sales = supabase.table("invoice_items") \
        .select("product_id, quantity, invoices!inner(invoice_date, store_id)") \
        .eq("invoices.store_id", STORE_ID) \
        .gte("invoices.invoice_date", cutoff_90) \
        .execute().data

    agg = {}
    for row in sales:
        pid = row.get("product_id")
        if not pid:
            continue
        entry = agg.setdefault(pid, {"days": set(), "units": 0})
        entry["days"].add(row["invoices"]["invoice_date"])
        entry["units"] += row.get("quantity") or 0

    summary = { "fast": 0, "moderate": 0, "slow": 0, "dead_stock": 0,
                "skipped_grace": 0, "unchanged_sold_out": 0 }
    updates = []

    for p in products:
        created_at = p.get("created_at")
        if created_at:
            created_date = date.fromisoformat(created_at[:10])
            if created_date > grace_cutoff:
                summary["skipped_grace"] += 1
                continue

        entry        = agg.get(p["id"])
        selling_days = len(entry["days"]) if entry else 0
        total_units  = entry["units"] if entry else 0

        if selling_days == 0 and total_units == 0:
            if (p.get("stock_quantity") or 0) > 0:
                new_type = "dead_stock"
            else:
                summary["unchanged_sold_out"] += 1
                continue
        elif selling_days >= 8 or total_units >= 30:
            new_type = "fast"
        elif selling_days >= 3 or total_units >= 10:
            new_type = "moderate"
        else:
            new_type = "slow"

        summary[new_type] += 1
        if new_type != p.get("product_type"):
            updates.append({
                "id": p["id"],
                "product_type": new_type,
                "reorder_level": REORDER_LEVELS[new_type],
            })

    for u in updates:
        supabase.table("products").update({
            "product_type":  u["product_type"],
            "reorder_level": u["reorder_level"],
            "type_last_calculated": date.today().isoformat(),
        }).eq("id", u["id"]).execute()

    return {
        "updated_count": len(updates),
        "total_checked": len(products),
        "summary": summary,
    }