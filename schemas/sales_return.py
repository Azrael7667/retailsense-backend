from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import date

class SalesReturnItemIn(BaseModel):
    product_id: Optional[UUID] = None
    invoice_item_id: Optional[UUID] = None  # fallback for invoice lines with no product_id
    quantity_returned: float
    restock_flag: bool = True

class SalesReturnCreate(BaseModel):
    invoice_id: UUID
    return_date: date
    reason: Optional[str] = None
    items: List[SalesReturnItemIn]
