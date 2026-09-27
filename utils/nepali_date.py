"""
Bikram Sambat (BS) <-> Gregorian (AD) date conversion.

Previously used a hand-maintained lookup table (BS_CALENDAR_DATA) that turned
out to have silent day-count errors accumulating into a real drift (BS year
2063 alone summed to 364 days instead of 365 - and there were more errors
elsewhere, since the total observed drift by 2083 was 5 days, not 1).

Replaced with `samaya` (pip install samaya) - an actively maintained library
with verified calendar data for BS 2000-2099. Same function signatures as
before (bs_to_ad, parse_bs_string_to_ad) so nothing else in the app needs to
change - only this file's internals differ.
"""

from datetime import date, datetime
from typing import Optional

from samaya import bs_to_ad as _samaya_bs_to_ad


def bs_to_ad(bs_year: int, bs_month: int, bs_day: int) -> Optional[date]:
    """
    Converts a BS date (year, month, day) to an AD date.
    Returns None if the date is invalid or outside samaya's supported
    range (BS 2000-2099).
    """
    try:
        bs_string = f"{bs_year:04d}-{bs_month:02d}-{bs_day:02d}"
        ad_string = _samaya_bs_to_ad(bs_string)  # returns 'YYYY-MM-DD' or similar
        return datetime.strptime(str(ad_string).strip(), "%Y-%m-%d").date()
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
