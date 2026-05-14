from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class CustomerBase(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    pan_number: Optional[str] = None
    credit_limit: float = 0.0

class CustomerCreate(CustomerBase):
    pass

class CustomerUpdate(CustomerBase):
    name: Optional[str] = None

class CustomerOut(CustomerBase):
    id: UUID
    store_id: UUID
    balance: float

    class Config:
        from_attributes = True
