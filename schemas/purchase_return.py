from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import date


class PurchaseReturnItemIn(BaseModel):
    purchase_item_id: UUID
    quantity_returned: float


class PurchaseReturnCreate(BaseModel):
    purchase_id: UUID
    return_date: date
    reason: Optional[str] = None
    items: List[PurchaseReturnItemIn]
