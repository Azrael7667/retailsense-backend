import math

from fastapi import APIRouter, Depends

from middleware.auth_middleware import get_active_store_id, get_current_user
from services import ai_results as ai

router = APIRouter()

# Shop policy, not a model output: flag a product when its stock covers fewer than this many weeks of expected sales.
# The owner orders from the big suppliers 2 to 3 times a week and from the rest at least weekly, so about 2 weeks
# (order gap plus delivery). Change it here, or call the endpoint with ?cover_weeks=N. No retraining needed.
RESTOCK_COVER_WEEKS = 2.0
RESTOCK_TARGET_WEEKS = 4.0      # when ordering, order enough to cover this many weeks


@router.get("/inventory-demand/status")
async def inventory_status(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    return ai.status("restock", store_id)


@router.get("/inventory-demand")
async def inventory_demand(only_needs_restock: bool = False, cover_weeks: float = RESTOCK_COVER_WEEKS,
                           target_weeks: float = RESTOCK_TARGET_WEEKS,
                           user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    d = ai.require("restock", store_id)
    recs = []
    for r in d["recommendations"]:
        x = dict(r)
        x["predictions"] = [p["qty"] if isinstance(p, dict) else p for p in r.get("predictions", [])]   # plain numbers, as the page expects
        demand, stock = float(r["next_4w_demand"]), float(r["current_stock"])
        weekly = demand / 4
        needs = bool(demand >= 1 and weekly > 0 and stock / weekly < cover_weeks)
        x["needs_restock"] = needs
        x["suggested_order"] = int(math.ceil(max(0.0, weekly * target_weeks - stock))) if needs else 0
        if only_needs_restock and not needs:
            continue
        recs.append(x)
    recs.sort(key=lambda r: (not r["needs_restock"], r["weeks_of_stock"], r["product_name"]))
    n = sum(r["needs_restock"] for r in recs)
    out = {k: v for k, v in d.items() if k not in ("recommendations", "summary", "cover_rule")}
    out["cover_rule"] = f"needs restock = stock covers fewer than {cover_weeks:g} weeks of expected sales; order up to {target_weeks:g} weeks"
    out["summary"] = {"total_products": len(d["recommendations"]), "needs_restock": n, "healthy_stock": len(d["recommendations"]) - n}
    out["recommendations"] = recs
    return out


@router.post("/inventory-demand/train")
async def train_inventory(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    ai.require("restock", store_id)
    return ai.TRAIN_MESSAGE
