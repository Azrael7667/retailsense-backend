from fastapi import APIRouter, Depends

from middleware.auth_middleware import get_active_store_id, get_current_user
from services import ai_results as ai

router = APIRouter()


@router.get("/sales-trend/status")
async def sales_trend_status(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    return ai.status("sales_trend", store_id)


@router.get("/sales-trend")
async def sales_trend(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    return ai.require("sales_trend", store_id)


@router.post("/sales-trend/train")
async def train_sales_trend(user=Depends(get_current_user), store_id: str = Depends(get_active_store_id)):
    ai.require("sales_trend", store_id)
    return ai.TRAIN_MESSAGE
