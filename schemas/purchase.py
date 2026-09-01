from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import date

class PurchaseItemIn(BaseModel):
    product_id: Optional[UUID] = None
    product_name: str
    quantity: float
    unit_price: float
    discount_percent: float = 0.0

class PurchaseCreate(BaseModel):
    supplier_id: Optional[UUID] = None
    bill_number: Optional[str] = None
    purchase_date: date
    tax: float = 0.0
    notes: Optional[str] = None
    items: List[PurchaseItemIn]

class PurchaseOut(BaseModel):
    id: UUID
    store_id: UUID
    bill_number: Optional[str]
    purchase_date: date
    subtotal: float
    discount_total: float = 0.0
    tax: float
    total: float
    paid_amount: float
    status: str

    class Config:
        from_attributes = True
