"""Feature engineering utilities for all ML models — filled in Phase 5"""
import pandas as pd


def build_sales_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add time-based features to sales dataframe"""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["dayofweek"] = df["date"].dt.dayofweek
    df["month"]     = df["date"].dt.month
    df["quarter"]   = df["date"].dt.quarter
    df["is_weekend"] = df["dayofweek"].isin([5, 6]).astype(int)
    return df


def add_nepali_holidays() -> pd.DataFrame:
    """Shared Nepali festival calendar for all Prophet-based models.

    Generates approximate month/day festival dates across last/this/next
    year so training + forecast windows always have holiday coverage,
    regardless of what year the data actually spans.
    """
    this_year = pd.Timestamp.now().year
    years = [this_year - 1, this_year, this_year + 1]

    rows = []
    for y in years:
        rows += [
            ("Dashain", f"{y}-10-07"), ("Dashain", f"{y}-10-14"), ("Dashain", f"{y}-10-21"),
            ("Tihar",   f"{y}-10-28"), ("Tihar",   f"{y}-11-04"),
            ("Nepali_New_Year", f"{y}-04-08"),
            ("Holi", f"{y}-03-25"),
            ("Maghe_Sankranti", f"{y}-01-15"),
        ]

    return pd.DataFrame({
        "holiday": [r[0] for r in rows],
        "ds": pd.to_datetime([r[1] for r in rows]),
        "lower_window": -1,
        "upper_window": [1 if r[0] != "Tihar" else 2 for r in rows],
    })
