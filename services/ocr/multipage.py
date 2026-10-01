from typing import Dict, List

from services.ocr.parser import parse_bill
from services.ocr.validator import validate_bill


def merge_pages(texts: List[str]) -> Dict:
    """Parse each page, merge header (first value found wins) and items, validate once."""
    parsed = [parse_bill(t) for t in texts]
    fields: Dict = {}
    for p in parsed:
        for k, v in p["fields"].items():
            if fields.get(k) is None and v is not None:
                fields[k] = v
    items = [i for p in parsed for i in p["items"]]
    val = validate_bill(fields, items, "\n".join(texts))
    val["pages"] = len(texts)
    return val
