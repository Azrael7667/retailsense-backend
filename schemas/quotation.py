from datetime import date
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

QuotationStatus = Literal["draft", "sent", "accepted", "rejected", "expired", "converted"]


class QuotationItemIn(BaseModel):
    product_id: Optional[UUID] = None
    product_name: str
    quantity: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    discount: float = Field(default=0.0, ge=0)   # per-line amount, same as invoice items


class QuotationCreate(BaseModel):
    customer_id: Optional[UUID] = None
    quotation_date: date
    valid_until: Optional[date] = None
    discount: float = 0.0
    tax: float = 0.0
    notes: Optional[str] = None
    terms: Optional[str] = None
    items: List[QuotationItemIn]
    quotation_number_mode: str = "auto"    # "auto" | "manual"
    quotation_number: Optional[str] = None


class QuotationStatusUpdate(BaseModel):
    status: QuotationStatus


class QuotationConvert(BaseModel):
    invoice_date: Optional[date] = None    # defaults to today
    payment_method: str = "cash"
    paid_amount: float = 0.0
