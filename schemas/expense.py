from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import date

class ExpenseCreate(BaseModel):
    category: Optional[str] = None
    amount: float
    description: Optional[str] = None
    expense_date: date

class ExpenseOut(ExpenseCreate):
    id: UUID
    store_id: UUID

    class Config:
        from_attributes = True
