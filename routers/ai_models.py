from fastapi import APIRouter, Depends
from middleware.auth_middleware import get_current_user
from models.store_helper import get_store_id

router = APIRouter()

@router.get("/cash-flow-forecast")
async def cash_flow_forecast(days: int = 30, user=Depends(get_current_user)):
    """Stub — Prophet model trained in Phase 5"""
    return {"message": "Cash flow forecast model not yet trained", "days": days}

@router.get("/inventory-demand")
async def inventory_demand(product_id: str, user=Depends(get_current_user)):
    """Stub — XGBoost/LightGBM model trained in Phase 5"""
    return {"message": "Inventory demand model not yet trained", "product_id": product_id}

@router.get("/customer-churn")
async def customer_churn(user=Depends(get_current_user)):
    """Stub — XGBoost + SHAP model trained in Phase 5"""
    return {"message": "Customer churn model not yet trained"}

@router.get("/sales-trend")
async def sales_trend(user=Depends(get_current_user)):
    """Stub — Prophet + Optuna model trained in Phase 5"""
    return {"message": "Sales trend model not yet trained"}

@router.get("/anomaly-detection")
async def anomaly_detection(user=Depends(get_current_user)):
    """Stub — Isolation Forest model trained in Phase 5"""
    return {"message": "Anomaly detection model not yet trained"}

@router.get("/credit-scoring/{customer_id}")
async def credit_scoring(customer_id: str, user=Depends(get_current_user)):
    """Stub — LightGBM + SHAP model trained in Phase 5"""
    return {"message": "Credit scoring model not yet trained", "customer_id": customer_id}
