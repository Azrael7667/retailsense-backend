                   
import os
import json
import joblib
import pandas as pd
import numpy as np
from datetime import date
from dotenv import load_dotenv
from supabase import create_client
from prophet import Prophet
import optuna
import warnings
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models_saved")
os.makedirs(MODEL_DIR, exist_ok=True)


def fetch_data(store_id):
    print("  Fetching all invoices (paginated)...")
    all_inv = []
    page    = 0
    while True:
        result = supabase.table("invoices") \
            .select("invoice_date, total") \
            .eq("store_id", store_id) \
            .eq("status", "paid") \
            .range(page * 1000, (page + 1) * 1000 - 1) \
            .execute()
        all_inv.extend(result.data)
        if len(result.data) < 1000:
            break
        page += 1

    df = pd.DataFrame(all_inv)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df = df[df["invoice_date"].dt.year == 2024]

    # Weekly aggregation
    df["week"] = df["invoice_date"].dt.to_period("W-MON").apply(lambda x: x.start_time)
    weekly     = df.groupby("week")["total"].sum().reset_index()
    weekly.columns = ["ds", "y"]
    weekly["ds"] = pd.to_datetime(weekly["ds"])
    weekly = weekly.sort_values("ds").reset_index(drop=True)

    print(f"  {len(weekly)} weeks of data")
    print(f"  Avg weekly sales: Rs {weekly['y'].mean():,.0f}")
    print(f"  Best week:        Rs {weekly['y'].max():,.0f}")
    print(f"  Worst week:       Rs {weekly['y'].min():,.0f}")
    return weekly


def add_nepali_holidays():
    return pd.DataFrame({
        "holiday": ["Dashain","Dashain","Tihar","Tihar","Nepali_New_Year","Holi"],
        "ds": pd.to_datetime([
            "2024-10-07","2024-10-14",
            "2024-10-28","2024-11-04",
            "2024-04-08","2024-03-25",
        ]),
        "lower_window": [-1,-1,-1,-1,-1,-1],
        "upper_window": [ 2, 1, 2, 1, 1, 1],
    })


def objective(trial, df):
    """Optuna objective — minimize MAE on last 4 weeks"""
    cps = trial.suggest_float("changepoint_prior_scale", 0.01, 0.5, log=True)
    sps = trial.suggest_float("seasonality_prior_scale", 1.0, 20.0)
    hps = trial.suggest_float("holidays_prior_scale",    5.0, 50.0)

    train = df.iloc[:-4].copy()
    valid = df.iloc[-4:].copy()

    train["is_monsoon"]  = train["ds"].dt.month.isin([6,7,8]).astype(int)
    train["is_festival"] = train["ds"].dt.month.isin([10,11]).astype(int)

    try:
        m = Prophet(
            holidays                = add_nepali_holidays(),
            yearly_seasonality      = True,
            weekly_seasonality      = False,
            daily_seasonality       = False,
            seasonality_mode        = "multiplicative",
            changepoint_prior_scale = cps,
            seasonality_prior_scale = sps,
            holidays_prior_scale    = hps,
        )
        m.add_regressor("is_monsoon")
        m.add_regressor("is_festival")
        m.fit(train)

        future = m.make_future_dataframe(periods=4, freq="W")
        future["is_monsoon"]  = future["ds"].dt.month.isin([6,7,8]).astype(int)
        future["is_festival"] = future["ds"].dt.month.isin([10,11]).astype(int)

        fc   = m.predict(future)
        preds = fc.tail(4)["yhat"].clip(lower=0).values
        mae  = np.mean(np.abs(preds - valid["y"].values))
        return mae
    except:
        return 1e9


def train_with_optuna(df, n_trials=30):
    print(f"  Running Optuna ({n_trials} trials)...")
    study = optuna.create_study(direction="minimize")
    study.optimize(lambda trial: objective(trial, df), n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    print(f"  Best params: cps={best['changepoint_prior_scale']:.3f}, "
          f"sps={best['seasonality_prior_scale']:.1f}, "
          f"hps={best['holidays_prior_scale']:.1f}")
    print(f"  Best MAE: Rs {study.best_value:,.0f}")
    return best


def train_final_model(df, best_params):
    print("  Training final Prophet model with best params...")
    train_df = df.copy()
    train_df["is_monsoon"]  = train_df["ds"].dt.month.isin([6,7,8]).astype(int)
    train_df["is_festival"] = train_df["ds"].dt.month.isin([10,11]).astype(int)

    model = Prophet(
        holidays                = add_nepali_holidays(),
        yearly_seasonality      = True,
        weekly_seasonality      = False,
        daily_seasonality       = False,
        seasonality_mode        = "multiplicative",
        changepoint_prior_scale = best_params["changepoint_prior_scale"],
        seasonality_prior_scale = best_params["seasonality_prior_scale"],
        holidays_prior_scale    = best_params["holidays_prior_scale"],
        interval_width          = 0.90,
    )
    model.add_regressor("is_monsoon")
    model.add_regressor("is_festival")
    model.fit(train_df)
    print("  Model trained.")
    return model, train_df


def analyze_trends(df, model, train_df):
    """Extract trend insights"""
    future = model.make_future_dataframe(periods=8, freq="W")
    future["is_monsoon"]  = future["ds"].dt.month.isin([6,7,8]).astype(int)
    future["is_festival"] = future["ds"].dt.month.isin([10,11]).astype(int)

    fc = model.predict(future)

    # Overall trend direction
    first_half = df["y"].iloc[:len(df)//2].mean()
    second_half= df["y"].iloc[len(df)//2:].mean()
    trend_pct  = ((second_half - first_half) / first_half * 100) if first_half > 0 else 0

    # Best and worst months
    df2 = df.copy()
    df2["month"] = df2["ds"].dt.month
    monthly      = df2.groupby("month")["y"].mean()
    best_month   = int(monthly.idxmax())
    worst_month  = int(monthly.idxmin())
    months       = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    # Forecast next 8 weeks
    last_date = train_df["ds"].max()
    fc_future = fc[fc["ds"] > last_date][["ds","yhat","yhat_lower","yhat_upper"]].head(8)
    fc_future = fc_future.copy()
    fc_future["yhat"]       = fc_future["yhat"].clip(lower=0)
    fc_future["yhat_lower"] = fc_future["yhat_lower"].clip(lower=0)
    fc_future["yhat_upper"] = fc_future["yhat_upper"].clip(lower=0)

    forecast_data = []
    for _, row in fc_future.iterrows():
        forecast_data.append({
            "week":          str(row["ds"].date()),
            "revenue":       round(float(row["yhat"]), 2),
            "revenue_lower": round(float(row["yhat_lower"]), 2),
            "revenue_upper": round(float(row["yhat_upper"]), 2),
        })

    # Weekly performance (historical)
    weekly_perf = []
    for _, row in df.iterrows():
        weekly_perf.append({
            "week":    str(row["ds"].date()),
            "revenue": round(float(row["y"]), 2),
        })

    return {
        "trend_direction":  "growing" if trend_pct > 0 else "declining",
        "trend_percent":    round(trend_pct, 1),
        "avg_weekly_sales": round(float(df["y"].mean()), 2),
        "best_week_ever":   round(float(df["y"].max()), 2),
        "worst_week_ever":  round(float(df["y"].min()), 2),
        "best_month":       months[best_month - 1],
        "worst_month":      months[worst_month - 1],
        "forecast_8w":      forecast_data,
        "historical":       weekly_perf,
    }


def get_store_id():
    result = supabase.table("stores").select("id") \
        .eq("name", "Bijeta Auto Parts").single().execute()
    if not result.data:
        raise ValueError("Store not found")
    return result.data["id"]


def train(store_id=None):
    if not store_id:
        store_id = get_store_id()

    print(f"\nTraining Sales Trend Model for store: {store_id}")
    print("-" * 55)

    df          = fetch_data(store_id)
    best_params = train_with_optuna(df, n_trials=30)
    model, train_df = train_final_model(df, best_params)
    insights    = analyze_trends(df, model, train_df)

    # Save
    joblib.dump(model, os.path.join(MODEL_DIR, f"sales_trend_model_{store_id}.pkl"))
    meta = {
        "model":       "prophet_optuna",
        "version":     "1.0",
        "store_id":    store_id,
        "trained_on":  str(date.today()),
        "best_params": best_params,
        "insights":    insights,
    }
    with open(os.path.join(MODEL_DIR, f"sales_trend_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print("\n  Sales Trend Insights:")
    print(f"  Trend:           {insights['trend_direction']} ({insights['trend_percent']:+.1f}%)")
    print(f"  Avg weekly:      Rs {insights['avg_weekly_sales']:,.0f}")
    print(f"  Best month:      {insights['best_month']}")
    print(f"  Worst month:     {insights['worst_month']}")
    print(f"\n  8-Week Forecast:")
    for fc in insights["forecast_8w"][:4]:
        print(f"  {fc['week']}  Rs {fc['revenue']:>10,.0f}")

    return model, insights


if __name__ == "__main__":
    train()
