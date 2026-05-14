from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import date

class InvoiceItemIn(BaseModel):
    product_id: Optional[UUID] = None
    product_name: str
    quantity: float
    unit_price: float
    discount: float = 0.0

class InvoiceCreate(BaseModel):
    customer_id: Optional[UUID] = None
    invoice_date: date
    payment_method: str = "cash"
    discount: float = 0.0
    tax: float = 0.0
    notes: Optional[str] = None
    items: List[InvoiceItemIn]

class InvoiceOut(BaseModel):
    id: UUID
    store_id: UUID
    invoice_number: str
    invoice_date: date
    subtotal: float
    discount: float
    tax: float
    total: float
    paid_amount: float
    status: str
    payment_method: str

    class Config:
        from_attributes = True
