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
