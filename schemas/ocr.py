from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class OCRResponse(BaseModel):
    text: str
    lines: List[str]
    confidence: float      # mean word confidence, 0-100
    word_count: int
    language: str
    processing_ms: int
    engine: str = "tesseract"
    preprocessed: bool = False
    header: Optional[Dict[str, Any]] = None       # parsed + corrected fields
    items: Optional[List[Dict[str, Any]]] = None  # parsed line items
    validation: Optional[Dict[str, Any]] = None   # status, checks, corrections, needs_review
