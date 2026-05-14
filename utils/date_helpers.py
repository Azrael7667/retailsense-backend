from datetime import date, timedelta

def get_month_range(year: int, month: int):
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end

def get_fiscal_year_range(year: int):
    """Nepal fiscal year: Shrawan 1 (mid-July) to Ashadh end (mid-July)"""
    start = date(year, 7, 16)
    end   = date(year + 1, 7, 15)
    return start, end
