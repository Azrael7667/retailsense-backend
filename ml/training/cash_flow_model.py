

import os
import json
import joblib
import pandas as pd
import numpy as np
from datetime import date
from dotenv import load_dotenv
from supabase import create_client
from prophet import Prophet
from prophet.diagnostics import cross_validation, performance_metrics
import warnings
warnings.filterwarnings("ignore")

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models_saved")
os.makedirs(MODEL_DIR, exist_ok=True)


def fetch_all(table, store_id, filters=None):
    """Fetch ALL rows using pagination — bypasses Supabase 1000 row limit"""
    all_rows = []
    page     = 0
    while True:
        q = supabase.table(table).select("*") \
            .eq("store_id", store_id) \
            .range(page * 1000, (page + 1) * 1000 - 1)
        if filters:
            for key, val in filters.items():
                q = q.eq(key, val)
        result = q.execute()
        all_rows.extend(result.data)
        if len(result.data) < 1000:
            break
        page += 1
    return all_rows


def fetch_data(store_id: str) -> pd.DataFrame:
    print("  Fetching all invoice data (paginated)...")
    inv = fetch_all("invoices", store_id, {"status": "paid"})
    print(f"  Fetched {len(inv)} invoices")

    print("  Fetching expense data...")
    exp = fetch_all("expenses", store_id)

    inv_df = pd.DataFrame(inv)
    if inv_df.empty:
        raise ValueError("No invoice data found.")

    inv_df["invoice_date"] = pd.to_datetime(inv_df["invoice_date"])
    inv_df = inv_df[inv_df["invoice_date"] >= (pd.Timestamp.now() - pd.Timedelta(days=395))]

    # Weekly aggregation
    inv_df["week"] = inv_df["invoice_date"].dt.to_period("W-MON").apply(lambda x: x.start_time)
    weekly_rev     = inv_df.groupby("week")["total"].sum().reset_index()
    weekly_rev.columns = ["ds", "revenue"]
    weekly_rev["ds"] = pd.to_datetime(weekly_rev["ds"])

    # Weekly expenses
    exp_df = pd.DataFrame(exp)
    weekly_exp = pd.DataFrame(columns=["ds", "expenses"])
    if not exp_df.empty:
        exp_df["expense_date"] = pd.to_datetime(exp_df["expense_date"])
        exp_df = exp_df[exp_df["expense_date"] >= (pd.Timestamp.now() - pd.Timedelta(days=395))]
        if not exp_df.empty:
            exp_df["week"] = exp_df["expense_date"].dt.to_period("W-MON").apply(lambda x: x.start_time)
            weekly_exp = exp_df.groupby("week")["amount"].sum().reset_index()
            weekly_exp.columns = ["ds", "expenses"]
            weekly_exp["ds"] = pd.to_datetime(weekly_exp["ds"])

    # Fall back to an estimated flat weekly expense if there's no real
    # expense data in the training window (either the table was empty,
    # or every row in it fell outside the last ~13 months). Built from
    # weekly_rev's own ds values directly — an independently generated
    # date_range doesn't reliably land on the same period anchor as
    # to_period("W-MON").start_time, which silently produced zero
    # matching dates on merge and turned every expense value NaN.
    if weekly_exp.empty:
        weekly_exp = pd.DataFrame({"ds": weekly_rev["ds"].values, "expenses": 10500.0})

    df = weekly_rev.merge(weekly_exp, on="ds", how="left")
    df["expenses"]      = df["expenses"].fillna(df["expenses"].mean())
    df["net_cash_flow"] = df["revenue"] - df["expenses"]
    df = df.sort_values("ds").reset_index(drop=True)

    print(f"  Weekly data: {len(df)} weeks ({df['ds'].min().date()} to {df['ds'].max().date()})")
    print(f"  Avg weekly revenue:  Rs {df['revenue'].mean():,.0f}")
    print(f"  Avg weekly expenses: Rs {df['expenses'].mean():,.0f}")
    return df


def add_nepali_holidays():
    # Approximate month/day for each festival, repeated across the years our
    # training + forecast window can touch — was hardcoded to 2024-only dates,
    # which silently stopped applying once real data moved past that year.
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

    holidays = pd.DataFrame({
        "holiday": [r[0] for r in rows],
        "ds": pd.to_datetime([r[1] for r in rows]),
        "lower_window": -1,
        "upper_window": [1 if r[0] != "Tihar" else 2 for r in rows],
    })
    return holidays


def train_revenue_model(df: pd.DataFrame):
    print("  Training revenue Prophet model (weekly)...")
    train_df = df[["ds", "revenue"]].rename(columns={"revenue": "y"}).copy()
    train_df["is_monsoon"]  = train_df["ds"].dt.month.isin([6, 7, 8]).astype(int)
    train_df["is_festival"] = train_df["ds"].dt.month.isin([10, 11]).astype(int)
    train_df["is_q1"]       = train_df["ds"].dt.month.isin([1, 2, 3]).astype(int)

    model = Prophet(
        holidays                = add_nepali_holidays(),
        yearly_seasonality      = True,
        weekly_seasonality      = False,
        daily_seasonality       = False,
        seasonality_mode        = "multiplicative",
        changepoint_prior_scale = 0.15,
        seasonality_prior_scale = 15.0,
        holidays_prior_scale    = 30.0,
        interval_width          = 0.90,
    )
    model.add_regressor("is_monsoon")
    model.add_regressor("is_festival")
    model.add_regressor("is_q1")
    model.fit(train_df)
    print("  Revenue model trained.")
    return model, train_df


def get_expense_stats(df: pd.DataFrame):
    avg = df["expenses"].mean()
    std = df["expenses"].std()
    print(f"  Weekly expense avg: Rs {avg:,.0f} ± Rs {std:,.0f}")
    return avg, std


def make_forecast(rev_model, avg_expense, train_df, weeks=5):
    print(f"  Generating {weeks}-week forecast...")
    future = rev_model.make_future_dataframe(periods=weeks, freq="W")
    future["is_monsoon"]  = future["ds"].dt.month.isin([6, 7, 8]).astype(int)
    future["is_festival"] = future["ds"].dt.month.isin([10, 11]).astype(int)
    future["is_q1"]       = future["ds"].dt.month.isin([1, 2, 3]).astype(int)

    rev_fc    = rev_model.predict(future)
    last_date = train_df["ds"].max()

    rev_fut = rev_fc[rev_fc["ds"] > last_date][
        ["ds", "yhat", "yhat_lower", "yhat_upper"]
    ].head(weeks).copy()

    rev_fut["exp_yhat"]      = avg_expense
    rev_fut["yhat"]          = rev_fut["yhat"].clip(lower=0)
    rev_fut["yhat_lower"]    = rev_fut["yhat_lower"].clip(lower=0)
    rev_fut["yhat_upper"]    = rev_fut["yhat_upper"].clip(lower=0)
    rev_fut["net_cash_flow"] = rev_fut["yhat"] - rev_fut["exp_yhat"]

    # Expand to daily
    daily_rows = []
    for _, row in rev_fut.iterrows():
        week_start = row["ds"]
        for d in range(7):
            day = week_start + pd.Timedelta(days=d)
            daily_rows.append({
                "date":          str(day.date()),
                "revenue":       round(float(row["yhat"]) / 7, 2),
                "revenue_lower": round(float(row["yhat_lower"]) / 7, 2),
                "revenue_upper": round(float(row["yhat_upper"]) / 7, 2),
                "expenses":      round(float(row["exp_yhat"]) / 7, 2),
                "net_cash_flow": round(float(row["net_cash_flow"]) / 7, 2),
            })

    return rev_fut, pd.DataFrame(daily_rows[:30])


def evaluate_model(rev_model, df):
    print("  Evaluating model...")
    try:
        train_df = df[["ds", "revenue"]].rename(columns={"revenue": "y"}).copy()
        train_df["is_monsoon"]  = train_df["ds"].dt.month.isin([6, 7, 8]).astype(int)
        train_df["is_festival"] = train_df["ds"].dt.month.isin([10, 11]).astype(int)
        train_df["is_q1"]       = train_df["ds"].dt.month.isin([1, 2, 3]).astype(int)

        cv = cross_validation(
            rev_model,
            initial="26 weeks",
            period="4 weeks",
            horizon="4 weeks",
            parallel=None,
        )
        metrics = performance_metrics(cv)
        mae  = metrics["mae"].mean()
        rmse = metrics["rmse"].mean()
        print(f"  MAE:  Rs {mae:,.0f} per week")
        print(f"  RMSE: Rs {rmse:,.0f} per week")
        return {"mae": round(mae, 2), "rmse": round(rmse, 2)}
    except Exception as e:
        print(f"  Evaluation skipped: {e}")
        return {}


def save_model(rev_model, avg_expense, metrics, store_id):
    print("  Saving model...")
    joblib.dump(rev_model, os.path.join(MODEL_DIR, f"cash_flow_revenue_{store_id}.pkl"))
    meta = {
        "model":              "prophet_weekly",
        "version":            "3.0",
        "store_id":           store_id,
        "trained_on":         str(date.today()),
        "metrics":            metrics,
        "avg_weekly_expense": round(avg_expense, 2),
        "last_training_date": "2024-12-31",
        "frequency":          "weekly",
    }
    with open(os.path.join(MODEL_DIR, f"cash_flow_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("  Model saved.")


def get_store_id():
    result = supabase.table("stores").select("id") \
        .eq("name", "Bijeta Auto Parts").single().execute()
    if not result.data:
        raise ValueError("Store not found")
    return result.data["id"]


def train(store_id: str = None):
    if not store_id:
        store_id = get_store_id()

    print(f"\nTraining Cash Flow Model for store: {store_id}")
    print("-" * 55)

    df                  = fetch_data(store_id)
    rev_model, train_df = train_revenue_model(df)
    avg_expense, _      = get_expense_stats(df)
    metrics             = evaluate_model(rev_model, df)
    weekly_fc, daily_fc = make_forecast(rev_model, avg_expense, train_df, weeks=5)
    save_model(rev_model, avg_expense, metrics, store_id)

    print("\n  Weekly Forecast (Jan 2025):")
    print(f"  {'Week':<12} {'Revenue':>12} {'Expenses':>12} {'Net Cash':>12}")
    print("  " + "-" * 52)
    for _, row in weekly_fc.iterrows():
        sign = "+" if row["net_cash_flow"] >= 0 else ""
        print(f"  {str(row['ds'].date()):<12} "
              f"Rs {row['yhat']:>9,.0f} "
              f"Rs {row['exp_yhat']:>9,.0f} "
              f"Rs {sign}{row['net_cash_flow']:>9,.0f}")

    t_rev = weekly_fc["yhat"].sum()
    t_exp = weekly_fc["exp_yhat"].sum()
    t_net = weekly_fc["net_cash_flow"].sum()

    print(f"\n  30-Day Totals:")
    print(f"  Expected Revenue:       Rs {t_rev:>12,.0f}")
    print(f"  Expected Expenses:      Rs {t_exp:>12,.0f}")
    print(f"  Expected Net Cash Flow: Rs {t_net:>12,.0f}")
    print(f"  Avg daily revenue:      Rs {t_rev/30:>12,.0f}")

    return rev_model, avg_expense, weekly_fc, daily_fc


if __name__ == "__main__":
    train()
