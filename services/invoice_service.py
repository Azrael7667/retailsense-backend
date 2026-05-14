"""Invoice business logic helpers — expanded in Phase 3"""

def calculate_invoice_totals(items: list, discount: float = 0, tax_rate: float = 0) -> dict:
    subtotal = sum((item["quantity"] * item["unit_price"]) - item.get("discount", 0) for item in items)
    tax = round(subtotal * tax_rate / 100, 2)
    total = round(subtotal - discount + tax, 2)
    return {"subtotal": round(subtotal, 2), "tax": tax, "total": total}
