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
    paid_amount: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    notes: Optional[str] = None
    items: List[InvoiceItemIn]
    # Delivery / charges — stored separately from tax so they can be itemized on
    # the printed invoice; delivery_note holds the JSON-encoded charges list from
    # the "Add Charges" UI (kept as an opaque string, not modeled further here).
    delivery_charge: float = 0.0
    delivery_address: Optional[str] = None
    delivery_note: Optional[str] = None
    # Invoice numbering — "auto" generates a sequential INV-{year}-{seq} number;
    # "manual" requires invoice_number and the backend enforces per-store uniqueness.
    # Only meaningful on create — update_invoice ignores both fields and always
    # keeps the invoice's original number.
    invoice_number_mode: str = "auto"
    invoice_number: Optional[str] = None

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
