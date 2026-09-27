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
    paid_amount: float = 0.0
    tax: float = 0.0
    notes: Optional[str] = None
    items: List[PurchaseItemIn]
    # Header-level extra discount (Rs), on top of any per-item discount_percent
    # already netted into each item's price. Mirrors invoices' header `discount`.
    discount: float = 0.0
    # Misc charges (Rs) added on top of the subtotal, and a +/- round-off amount
    # folded into `tax` for the stored total but also kept here separately for
    # display/breakdown (matches what the purchase-create form already stores).
    charges_amount: float = 0.0
    round_off_amount: float = 0.0
    # Uploaded attachment URLs (Supabase Storage) — uploaded client-side before
    # this call; the backend just persists the resulting list of URLs.
    image_urls: List[str] = []
    # Bill numbering — "auto" generates a sequential BILL-{YYYYMM}-{seq} number
    # (month taken from purchase_date, not today's date); "manual" requires
    # bill_number and the backend enforces per-store uniqueness. Only meaningful
    # on create — update_purchase ignores both and always keeps the original number.
    bill_number_mode: str = "auto"

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
