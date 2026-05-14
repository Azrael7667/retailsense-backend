from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import date

class KhataEntryCreate(BaseModel):
    party_type: str       # 'customer' | 'supplier'
    party_id: UUID
    entry_type: str       # 'debit' | 'credit'
    amount: float
    description: Optional[str] = None
    entry_date: date

class KhataEntryOut(KhataEntryCreate):
    id: UUID
    store_id: UUID

    class Config:
        from_attributes = True
