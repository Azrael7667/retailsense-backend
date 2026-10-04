from fastapi import APIRouter, Depends

from middleware.auth_middleware import get_active_store_id, get_current_user
from services import ai_results as ai

router = APIRouter()


@router.get("/anomaly-detection/status")
async def anomaly_status(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    return ai.status("anomaly", store_id)


@router.get("/anomaly-detection")
async def anomaly_detection(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    d = ai.require("anomaly", store_id)
    info = ai.invoice_customers([a["invoice_id"] for a in d["anomalies"]], store_id)
    names = ai.customers(store_id)
    anoms = []
    for a in d["anomalies"]:
        x, row = dict(a), info.get(a["invoice_id"])
        if row:
            x["invoice_number"] = row.get("invoice_number") or x.get("invoice_number")
            c = names.get(row.get("customer_id"))
            x["customer_name"] = c["name"] if c else "Walk-in customer"
        anoms.append(x)
    return {**{k: v for k, v in d.items() if k != "anomalies"}, "anomalies": anoms}


@router.post("/anomaly-detection/train")
async def train_anomaly(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    ai.require("anomaly", store_id)
    return ai.TRAIN_MESSAGE
