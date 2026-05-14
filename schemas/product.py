from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class ProductBase(BaseModel):
    name: str
    sku: Optional[str] = None
    barcode: Optional[str] = None
    category_id: Optional[UUID] = None
    unit: str = "pcs"
    cost_price: float = 0.0
    selling_price: float = 0.0
    stock_quantity: float = 0.0
    reorder_level: float = 5.0
    image_url: Optional[str] = None
    is_active: bool = True

class ProductCreate(ProductBase):
    pass

class ProductUpdate(ProductBase):
    name: Optional[str] = None

class ProductOut(ProductBase):
    id: UUID
    store_id: UUID

    class Config:
        from_attributes = True
