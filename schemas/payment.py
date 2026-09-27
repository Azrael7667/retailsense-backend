from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import date

class PaymentCreate(BaseModel):
    customer_id: Optional[UUID] = None
    payment_date: date
    amount: float
    payment_method: str = "cash"
    reference: Optional[str] = None
    notes: Optional[str] = None
