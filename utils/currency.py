def format_rs(amount: float) -> str:
    """Format float as Nepali Rupees string: Rs 1,23,456.00"""
    return f"Rs {amount:,.2f}"
