"""
Bikram Sambat (BS) <-> Gregorian (AD) date conversion.

Mirrors the reference point and calendar table used in the frontend's
src/utils/dateHelpers.js (adToBS), but in reverse: this converts an
extracted BS date string (e.g. from a scanned bill) into a real AD date
Postgres can store. Keep BS_CALENDAR_DATA in sync with the frontend file
if either ever gets updated — they must describe the same calendar.
"""

from datetime import date, timedelta
from typing import Optional

BS_CALENDAR_DATA = {
    2057: [30,32,31,32,31,30,30,30,29,30,29,31],
    2058: [31,31,32,32,31,30,30,30,29,30,29,31],
    2059: [31,31,32,31,31,31,30,29,30,29,30,30],
    2060: [31,32,31,32,31,30,30,30,29,30,29,31],
    2061: [30,32,31,32,31,30,30,30,29,30,30,30],
    2062: [31,31,32,32,31,30,30,30,29,30,29,31],
    2063: [31,31,32,31,31,30,30,29,30,29,30,30],
    2064: [31,32,31,32,31,30,30,30,29,30,29,31],
    2065: [31,31,31,32,31,31,29,30,29,30,29,31],
    2066: [31,31,32,32,31,30,30,29,30,29,30,30],
    2067: [31,32,31,32,31,30,30,30,29,30,29,31],
    2068: [31,31,31,32,31,31,29,30,29,30,29,31],
    2069: [31,31,32,32,31,30,30,29,30,29,30,30],
    2070: [31,32,31,32,31,30,30,30,29,30,29,31],
    2071: [31,31,31,32,31,31,29,30,29,30,29,31],
    2072: [31,32,31,32,31,30,30,29,30,29,30,30],
    2073: [31,32,31,32,31,30,30,30,29,30,29,31],
    2074: [31,31,31,32,31,31,30,29,30,29,30,30],
    2075: [31,32,31,32,31,30,30,30,29,30,29,31],
    2076: [31,31,32,32,31,30,30,29,30,29,30,30],
    2077: [31,32,31,32,31,30,30,30,29,30,29,31],
    2078: [31,31,31,32,31,31,30,29,30,29,30,30],
    2079: [31,32,31,32,31,30,30,30,29,30,29,31],
    2080: [31,31,32,32,31,30,30,29,30,29,30,30],
    2081: [31,32,31,32,31,30,30,30,29,30,29,31],
    2082: [31,31,31,32,31,31,30,29,30,29,30,30],
    2083: [31,32,31,32,31,30,30,30,29,30,29,31],
    2084: [31,31,32,32,31,30,30,29,30,29,30,30],
    2085: [31,32,31,32,31,30,30,30,29,30,29,31],
}

# Same reference point as the frontend: 2000-01-01 AD = 2056-09-17 BS
AD_REF = date(2000, 1, 1)
BS_REF = {"year": 2056, "month": 9, "day": 17}


def _month_days_bs(year: int, month: int) -> int:
    data = BS_CALENDAR_DATA.get(year)
    if not data:
        return 30
    return data[month - 1] if 1 <= month <= 12 else 30


def bs_to_ad(bs_year: int, bs_month: int, bs_day: int) -> Optional[date]:
    """
    Converts a BS date (year, month, day) to an AD date.Coun
    Returns None if the BS year is outside our known calendar range
    (2057-2085 BS, matching the frontend's table) or the date is invalid.
    """
    if bs_year not in BS_CALENDAR_DATA:
        return None
    if not (1 <= bs_month <= 12):
        return None
    if not (1 <= bs_day <= _month_days_bs(bs_year, bs_month)):
        return None

    # Count total days from BS_REF to the target BS date
    days = 0
    y, m = BS_REF["year"], BS_REF["month"]

    if (bs_year, bs_month, bs_day) >= (y, m, BS_REF["day"]):
        # target is on/after reference — count forward
        d = BS_REF["day"]
        while (y, m) != (bs_year, bs_month):
            days += _month_days_bs(y, m) - d + 1
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
        days += bs_day - d
        return AD_REF + timedelta(days=days)
    else:
        # target is before reference — count backward
        d = BS_REF["day"]
        while (y, m) != (bs_year, bs_month):
            m -= 1
            if m < 1:
                m = 12
                y -= 1
            days += _month_days_bs(y, m)
        days += d - bs_day
        return AD_REF - timedelta(days=days)


def parse_bs_string_to_ad(bs_string: str) -> Optional[date]:
    """
    Takes a BS date string in 'YYYY-MM-DD' format (as Gemini extracts it
    from bills, e.g. "2081-12-30") and returns the equivalent AD date,
    or None if it can't be parsed/converted (out of known range, or the
    string wasn't valid BS to begin with).
    """
    if not bs_string:
        return None
    try:
        parts = bs_string.strip().split("-")
        if len(parts) != 3:
            return None
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        return bs_to_ad(year, month, day)
    except (ValueError, TypeError):
        return None
