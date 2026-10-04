"""
Bikram Sambat (BS) <-> Gregorian (AD) date conversion.

History: a hand-maintained lookup table drifted by days, and was replaced by `samaya`.
Testing against dates printed side by side (BS and AD) on 19 real supplier bills showed `samaya`
is one day late for BS 2083 months 5 and 6 (Bhadra and Ashwin), and a day-by-day comparison with a
second library showed the two disagree on many other months between BS 2070 and 2095.
The bills agree with `nepali-datetime`, so that library is used here.

Same function signatures as before (bs_to_ad, parse_bs_string_to_ad).
"""

from datetime import date
from typing import Optional

import nepali_datetime


def bs_to_ad(bs_year: int, bs_month: int, bs_day: int) -> Optional[date]:
    """
    Converts a BS date (year, month, day) to an AD date.
    Returns None if the date is invalid or outside the library's supported range (BS 1975-2100).
    """
    try:
        return nepali_datetime.date(int(bs_year), int(bs_month), int(bs_day)).to_datetime_date()
    except Exception:
        return None


def parse_bs_string_to_ad(bs_string: str) -> Optional[date]:
    """
    Takes a BS date string in 'YYYY-MM-DD' format (as Gemini extracts it
    from bills, e.g. "2081-12-30") and returns the equivalent AD date,
    or None if it can't be parsed/converted.
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
