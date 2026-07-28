

import os
import json
import joblib
import pandas as pd
import numpy as np
from datetime import date
from dotenv import load_dotenv
from supabase import create_client
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_absolute_error, mean_squared_error
import warnings
warnings.filterwarnings("ignore")

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models_saved")
os.makedirs(MODEL_DIR, exist_ok=True)


def fetch_all_paginated(table, store_id, extra_filters=None):
    """Fetch all rows bypassing 1000 row limit"""
    all_rows = []
    page     = 0
    while True:
        q = supabase.table(table).select("*") \
            .eq("store_id", store_id) \
            .range(page * 1000, (page + 1) * 1000 - 1)
        if extra_filters:
            for k, v in extra_filters.items():
                q = q.eq(k, v)
        result = q.execute()
        all_rows.extend(result.data)
        if len(result.data) < 1000:
            break
        page += 1
    return all_rows


def fetch_invoice_items(store_id):
    """Fetch all invoice items with their invoice dates"""
    print("  Fetching invoice items (paginated)...")
    all_items = []
    page      = 0
    while True:
        result = supabase.table("invoice_items") \
            .select("product_id, product_name, quantity, total, invoice_id, invoices(invoice_date, store_id)") \
            .range(page * 1000, (page + 1) * 1000 - 1) \
            .execute()
        # Filter by store
        for item in result.data:
            if item.get("invoices") and item["invoices"].get("store_id") == store_id:
                all_items.append(item)
        if len(result.data) < 1000:
            break
        page += 1
    print(f"  Fetched {len(all_items)} invoice items")
    return all_items


def build_features(df):
    """Build time-series features for LightGBM"""
    df = df.copy()
    df["week_of_year"] = df["ds"].dt.isocalendar().week.astype(int)
    df["month"]        = df["ds"].dt.month
    df["quarter"]      = df["ds"].dt.quarter
    df["is_monsoon"]   = df["month"].isin([6, 7, 8]).astype(int)
    df["is_festival"]  = df["month"].isin([10, 11]).astype(int)
    df["is_q1"]        = df["month"].isin([1, 2, 3]).astype(int)

    # Lag features — previous weeks demand
    df = df.sort_values("ds")
    df["lag_1"]  = df["qty"].shift(1)
    df["lag_2"]  = df["qty"].shift(2)
    df["lag_4"]  = df["qty"].shift(4)
    df["lag_8"]  = df["qty"].shift(8)

    # Rolling averages
    df["rolling_4w_mean"] = df["qty"].shift(1).rolling(4).mean()
    df["rolling_8w_mean"] = df["qty"].shift(1).rolling(8).mean()
    df["rolling_4w_std"]  = df["qty"].shift(1).rolling(4).std().fillna(0)

    # Trend
    df["trend"] = range(len(df))

    return df


FEATURE_COLS = [
    "week_of_year", "month", "quarter",
    "is_monsoon", "is_festival", "is_q1",
    "lag_1", "lag_2", "lag_4", "lag_8",
    "rolling_4w_mean", "rolling_8w_mean", "rolling_4w_std",
    "trend",
]


def train_product_model(product_name, weekly_df):
    """Train LightGBM for a single product"""
    df = build_features(weekly_df)
    df = df.dropna()

    if len(df) < 8:
        return None, None

    X = df[FEATURE_COLS]
    y = df["qty"]

    # Time series split — last 4 weeks as validation
    split_idx = max(4, len(df) - 4)
    X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

    model = lgb.LGBMRegressor(
        n_estimators      = 200,
        learning_rate     = 0.05,
        max_depth         = 4,
        num_leaves        = 15,
        min_child_samples = 5,
        subsample         = 0.8,
        colsample_bytree  = 0.8,
        reg_alpha         = 0.1,
        reg_lambda        = 0.1,
        random_state      = 42,
        verbose           = -1,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(period=-1)],
    )

    # Metrics
    if len(X_val) > 0:
        preds = model.predict(X_val).clip(0)
        mae   = mean_absolute_error(y_val, preds)
    else:
        mae = None

    return model, mae


def predict_next_weeks(model, weekly_df, n_weeks=4):
    """Predict demand for next N weeks"""
    df   = build_features(weekly_df).dropna()
    if df.empty or model is None:
        avg = weekly_df["qty"].mean()
        return [round(avg)] * n_weeks

    predictions = []
    last_row    = df.copy()

    for w in range(n_weeks):
        last_features = last_row[FEATURE_COLS].iloc[-1:].copy()
        # Update time features for next week
        last_ds = last_row["ds"].iloc[-1]
        next_ds = last_ds + pd.Timedelta(weeks=1)
        last_features["week_of_year"] = next_ds.isocalendar()[1]
        last_features["month"]        = next_ds.month
        last_features["quarter"]      = (next_ds.month - 1) // 3 + 1
        last_features["is_monsoon"]   = int(next_ds.month in [6, 7, 8])
        last_features["is_festival"]  = int(next_ds.month in [10, 11])
        last_features["is_q1"]        = int(next_ds.month in [1, 2, 3])
        last_features["trend"]        = last_features["trend"].values[0] + 1

        pred = float(model.predict(last_features)[0])
        pred = max(0, round(pred, 1))
        predictions.append(pred)

        # Add prediction as new row for next iteration
        new_row = last_row.iloc[[-1]].copy()
        new_row["ds"]  = next_ds
        new_row["qty"] = pred
        new_row = build_features(new_row)
        last_row = pd.concat([last_row, new_row], ignore_index=True)

    return predictions


def get_store_id():
    result = supabase.table("stores").select("id") \
        .eq("name", "Bijeta Auto Parts").single().execute()
    if not result.data:
        raise ValueError("Store not found")
    return result.data["id"]


def train(store_id: str = None):
    if not store_id:
        store_id = get_store_id()

    print(f"\nTraining Inventory Demand Model for store: {store_id}")
    print("-" * 55)

    # Fetch data
    items = fetch_invoice_items(store_id)
    if not items:
        raise ValueError("No invoice items found")

    # Build dataframe
    rows = []
    for item in items:
        if item.get("invoices") and item["invoices"].get("invoice_date"):
            rows.append({
                "product_id":   item["product_id"],
                "product_name": item["product_name"],
                "quantity":     float(item["quantity"]),
                "date":         pd.to_datetime(item["invoices"]["invoice_date"]),
            })

    df = pd.DataFrame(rows)
    df = df[df["date"].dt.year == 2024]
    print(f"  2024 items: {len(df)} rows, {df['product_name'].nunique()} unique products")

    # Weekly aggregation per product
    df["week"] = df["date"].dt.to_period("W-MON").apply(lambda x: x.start_time)
    weekly     = df.groupby(["product_name", "week"])["quantity"].sum().reset_index()
    weekly.columns = ["product_name", "ds", "qty"]
    weekly["ds"] = pd.to_datetime(weekly["ds"])

    # Get product list with stock info
    products = supabase.table("products").select(
        "id, name, stock_quantity, reorder_level, unit, cost_price, selling_price"
    ).eq("store_id", store_id).eq("is_active", True).execute().data

    prod_map = {p["name"]: p for p in products}

    # Train model per product
    print(f"\n  Training models for {len(prod_map)} products...")
    all_models    = {}
    all_forecasts = {}
    maes          = []

    for prod_name, prod_info in prod_map.items():
        prod_weekly = weekly[weekly["product_name"] == prod_name].sort_values("ds")

        if len(prod_weekly) < 4:
            # Not enough data — use simple average
            avg_qty = prod_weekly["qty"].mean() if len(prod_weekly) > 0 else 1.0
            all_forecasts[prod_name] = {
                "method":        "average",
                "predictions":   [round(avg_qty)] * 4,
                "avg_weekly_qty": round(avg_qty, 1),
            }
            continue

        model, mae = train_product_model(prod_name, prod_weekly)

        if model is None:
            avg_qty = prod_weekly["qty"].mean()
            all_forecasts[prod_name] = {
                "method":        "average",
                "predictions":   [round(avg_qty)] * 4,
                "avg_weekly_qty": round(avg_qty, 1),
            }
            continue

        predictions = predict_next_weeks(model, prod_weekly, n_weeks=4)
        avg_weekly  = prod_weekly["qty"].mean()

        all_models[prod_name]    = model
        all_forecasts[prod_name] = {
            "method":          "lightgbm",
            "predictions":     predictions,
            "avg_weekly_qty":  round(avg_weekly, 1),
            "mae":             round(mae, 2) if mae else None,
        }
        if mae:
            maes.append(mae)

    print(f"  LightGBM models: {len(all_models)}")
    print(f"  Average models:  {len(all_forecasts) - len(all_models)}")
    if maes:
        print(f"  Avg MAE: {np.mean(maes):.2f} units/week")

    # Save everything
    print("\n  Saving models...")
    joblib.dump(all_models, os.path.join(MODEL_DIR, f"inventory_models_{store_id}.pkl"))

    # Build restock recommendations
    recommendations = []
    for prod_name, fc in all_forecasts.items():
        prod_info    = prod_map.get(prod_name, {})
        current_stock = float(prod_info.get("stock_quantity", 0))
        reorder_level = float(prod_info.get("reorder_level", 5))
        unit          = prod_info.get("unit", "pcs")
        avg_weekly    = fc["avg_weekly_qty"]
        next_4w_demand = sum(fc["predictions"])
        weeks_of_stock = current_stock / avg_weekly if avg_weekly > 0 else 99
        needs_restock  = current_stock <= reorder_level or weeks_of_stock < 2

        recommendations.append({
            "product_name":    prod_name,
            "unit":            unit,
            "current_stock":   float(current_stock),
            "reorder_level":   reorder_level,
            "avg_weekly_qty":  avg_weekly,
            "next_4w_demand":  round(next_4w_demand, 1),
            "weeks_of_stock":  round(weeks_of_stock, 1),
            "needs_restock":   bool(needs_restock),
            "suggested_order": round(max(0, next_4w_demand - current_stock + reorder_level), 1),
            "predictions":     fc["predictions"],
            "method":          fc["method"],
        })

    recommendations.sort(key=lambda x: (not x["needs_restock"], x["weeks_of_stock"]))

    meta = {
        "model":      "lightgbm_per_product",
        "version":    "1.0",
        "store_id":   store_id,
        "trained_on": str(date.today()),
        "n_products": len(all_forecasts),
        "avg_mae":    round(np.mean(maes), 2) if maes else None,
        "features":   FEATURE_COLS,
    }

    with open(os.path.join(MODEL_DIR, f"inventory_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)

    with open(os.path.join(MODEL_DIR, f"inventory_forecasts_{store_id}.json"), "w") as f:
        json.dump({"recommendations": recommendations, "forecasts": all_forecasts}, f, indent=2)

    print("  Models saved.")

    # Print top restock alerts
    urgent = [r for r in recommendations if r["needs_restock"]]
    print(f"\n  Restock Alerts ({len(urgent)} products need restocking):")
    print(f"  {'Product':<35} {'Stock':>6} {'4W Demand':>10} {'Order':>8}")
    print("  " + "-" * 65)
    for r in urgent[:10]:
        print(f"  {r['product_name'][:35]:<35} "
              f"{r['current_stock']:>6.0f} "
              f"{r['next_4w_demand']:>10.0f} "
              f"{r['suggested_order']:>8.0f} {r['unit']}")

    print(f"\n  Healthy stock ({len(recommendations) - len(urgent)} products):")
    healthy = [r for r in recommendations if not r["needs_restock"]]
    for r in healthy[:5]:
        print(f"  {r['product_name'][:35]:<35} stock: {r['current_stock']:.0f} {r['unit']} ({r['weeks_of_stock']:.1f} weeks)")

    return all_models, recommendations


if __name__ == "__main__":
    train()
