from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class SupplierBase(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    pan_number: Optional[str] = None

class SupplierCreate(SupplierBase):
    pass

class SupplierUpdate(SupplierBase):
    name: Optional[str] = None

class SupplierOut(SupplierBase):
    id: UUID
    store_id: UUID
    balance: float

    class Config:
        from_attributes = True
