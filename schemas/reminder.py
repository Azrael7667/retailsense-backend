from pydantic import BaseModel
from typing import Optional
from datetime import date
from uuid import UUID

class ReminderCreate(BaseModel):
    party_type: str   # "customer" | "supplier"
    party_id: UUID
    remind_date: date
    note: Optional[str] = None
