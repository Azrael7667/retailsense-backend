import os
import json
import joblib
import pandas as pd
import numpy as np
from datetime import date
from dotenv import load_dotenv
from supabase import create_client
import lightgbm as lgb
from sklearn.metrics import mean_absolute_error
import warnings
warnings.filterwarnings("ignore")

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models_saved")
os.makedirs(MODEL_DIR, exist_ok=True)

FEATURE_COLS = [
    "week_of_year", "month", "quarter",
    "is_monsoon", "is_festival", "is_q1",
    "lag_1", "lag_2", "lag_4", "lag_8",
    "rolling_4w_mean", "rolling_8w_mean", "rolling_4w_std",
    "trend", "group_code",
]
CATEGORICAL_FEATURES = ["group_code"]


def fetch_store_invoice_ids(store_id):
    all_ids, page = [], 0
    while True:
        result = supabase.table("invoices") \
            .select("id") \
            .eq("store_id", store_id) \
            .eq("status", "paid") \
            .range(page * 1000, (page + 1) * 1000 - 1) \
            .execute()
        all_ids.extend([r["id"] for r in result.data])
        if len(result.data) < 1000:
            break
        page += 1
    return all_ids


def fetch_invoice_items(store_id):
    print("  Fetching store's invoice IDs...")
    invoice_ids = fetch_store_invoice_ids(store_id)
    print(f"  {len(invoice_ids)} paid invoices for this store")

    print("  Fetching invoice items (paginated, scoped to store)...")
    all_items = []
    CHUNK = 200
    for i in range(0, len(invoice_ids), CHUNK):
        chunk_ids = invoice_ids[i:i + CHUNK]
        page = 0
        while True:
            result = supabase.table("invoice_items") \
                .select("product_id, product_name, quantity, total, invoice_id, invoices(invoice_date)") \
                .in_("invoice_id", chunk_ids) \
                .range(page * 1000, (page + 1) * 1000 - 1) \
                .execute()
            all_items.extend(result.data)
            if len(result.data) < 1000:
                break
            page += 1
    print(f"  Fetched {len(all_items)} invoice items")
    return all_items


def fetch_categories(store_id):
    result = supabase.table("categories").select("id, name").eq("store_id", store_id).execute()
    return {c["id"]: c["name"] for c in result.data}


def reindex_group(group_weekly, all_weeks, group_key, key_col):
    s = group_weekly.set_index("ds")["qty"].reindex(all_weeks, fill_value=0)
    out = s.reset_index()
    out.columns = ["ds", "qty"]
    out[key_col] = group_key
    return out


def build_pooled_dataset(weekly, group_keys, key_col):
    """Reindex every group onto the full store-wide weekly calendar
    (zero-filled), built from dates that actually occur in the data —
    NOT a freshly generated pd.date_range, since date_range(freq='W-MON')
    lands on Mondays while to_period('W-MON').start_time lands on
    Tuesdays. Mixing the two silently zeroes out every real value."""
    all_weeks = pd.DatetimeIndex(sorted(weekly["ds"].unique()))
    frames = []
    for key in group_keys:
        group_weekly = weekly[weekly[key_col] == key][["ds", "qty"]]
        frames.append(reindex_group(group_weekly, all_weeks, key, key_col))
    return pd.concat(frames, ignore_index=True)


def build_features(df, key_col):
    df = df.copy()
    df["week_of_year"] = df["ds"].dt.isocalendar().week.astype(int)
    df["month"]        = df["ds"].dt.month
    df["quarter"]      = df["ds"].dt.quarter
    df["is_monsoon"]   = df["month"].isin([6, 7, 8]).astype(int)
    df["is_festival"]  = df["month"].isin([10, 11]).astype(int)
    df["is_q1"]        = df["month"].isin([1, 2, 3]).astype(int)

    df = df.sort_values([key_col, "ds"])
    g = df.groupby(key_col)["qty"]
    df["lag_1"] = g.shift(1)
    df["lag_2"] = g.shift(2)
    df["lag_4"] = g.shift(4)
    df["lag_8"] = g.shift(8)
    df["rolling_4w_mean"] = df.groupby(key_col)["qty"].transform(lambda s: s.shift(1).rolling(4).mean())
    df["rolling_8w_mean"] = df.groupby(key_col)["qty"].transform(lambda s: s.shift(1).rolling(8).mean())
    df["rolling_4w_std"]  = df.groupby(key_col)["qty"].transform(lambda s: s.shift(1).rolling(4).std()).fillna(0)
    df["trend"] = df.groupby(key_col).cumcount()
    return df


def train_pooled_model(panel_df, key_col):
    """One LightGBM model pooled across all groups (categories), Tweedie
    objective for zero-inflated demand data."""
    df = build_features(panel_df, key_col)
    df["group_code"] = df[key_col].astype("category")
    categories = df["group_code"].cat.categories.tolist()

    df = df.dropna(subset=["lag_8"])
    if df.empty:
        return None, {"error": "not enough pooled history"}, categories

    pct_zero = float((df["qty"] == 0).mean())
    print(f"  Zero-demand rows in training panel: {pct_zero*100:.1f}%")

    cutoff = df["ds"].max() - pd.Timedelta(weeks=4)
    train_df = df[df["ds"] <= cutoff]
    val_df   = df[df["ds"] >  cutoff]

    X_train, y_train = train_df[FEATURE_COLS], train_df["qty"]
    X_val,   y_val   = val_df[FEATURE_COLS],   val_df["qty"]

    model = lgb.LGBMRegressor(
        objective               = "tweedie",
        tweedie_variance_power  = 1.2,
        n_estimators            = 400,
        learning_rate           = 0.05,
        max_depth               = 5,
        num_leaves              = 20,
        min_child_samples       = 10,
        subsample                = 0.8,
        colsample_bytree         = 0.8,
        reg_alpha                = 0.1,
        reg_lambda                = 0.1,
        random_state              = 42,
        verbose                    = -1,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        categorical_feature=CATEGORICAL_FEATURES,
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(period=-1)],
    )

    metrics = {"n_train_rows": int(len(train_df)), "n_val_rows": int(len(val_df)), "pct_zero_actual": round(pct_zero, 4)}
    if len(y_val) > 0:
        preds = model.predict(X_val).clip(min=0)
        mae   = mean_absolute_error(y_val, preds)
        wmape = np.abs(y_val.values - preds).sum() / max(y_val.values.sum(), 1e-9)
        metrics["mae"]   = round(float(mae), 3)
        metrics["wmape"] = round(float(wmape), 4)
        print(f"  Validation MAE:   {mae:.2f} units/week")
        print(f"  Validation WMAPE: {wmape*100:.1f}%")

        nonzero_mask = y_val.values > 0
        if nonzero_mask.sum() > 0:
            mae_nz   = mean_absolute_error(y_val.values[nonzero_mask], preds[nonzero_mask])
            wmape_nz = np.abs(y_val.values[nonzero_mask] - preds[nonzero_mask]).sum() / max(y_val.values[nonzero_mask].sum(), 1e-9)
            metrics["mae_nonzero_weeks"]   = round(float(mae_nz), 3)
            metrics["wmape_nonzero_weeks"] = round(float(wmape_nz), 4)
            metrics["n_nonzero_val_rows"]  = int(nonzero_mask.sum())
            print(f"  Validation MAE (non-zero weeks):   {mae_nz:.2f} units/week  [{nonzero_mask.sum()} rows]")
            print(f"  Validation WMAPE (non-zero weeks): {wmape_nz*100:.1f}%")

    return model, metrics, categories


def predict_next_weeks_group(model, categories, group_panel_df, group_key, key_col, n_weeks=4):
    if model is None or group_key not in categories:
        avg = group_panel_df["qty"].mean() if not group_panel_df.empty else 0.0
        return [round(avg, 1)] * n_weeks

    df = build_features(group_panel_df, key_col)
    if df.dropna(subset=["lag_8"]).empty:
        avg = group_panel_df["qty"].mean()
        return [round(avg, 1)] * n_weeks

    last_row = df.copy()
    predictions = []
    for _ in range(n_weeks):
        feat_row = last_row[["ds"] + FEATURE_COLS[:-1]].iloc[-1:].copy()
        last_ds = last_row["ds"].iloc[-1]
        next_ds = last_ds + pd.Timedelta(weeks=1)
        feat_row["week_of_year"] = next_ds.isocalendar()[1]
        feat_row["month"]        = next_ds.month
        feat_row["quarter"]      = (next_ds.month - 1) // 3 + 1
        feat_row["is_monsoon"]   = int(next_ds.month in [6, 7, 8])
        feat_row["is_festival"]  = int(next_ds.month in [10, 11])
        feat_row["is_q1"]        = int(next_ds.month in [1, 2, 3])
        feat_row["trend"]        = feat_row["trend"].values[0] + 1
        feat_row["group_code"]   = pd.Categorical([group_key], categories=categories)

        pred = max(0, round(float(model.predict(feat_row[FEATURE_COLS])[0]), 2))
        predictions.append(pred)

        new_row = pd.DataFrame([{"ds": next_ds, "qty": pred, key_col: group_key}])
        last_row = build_features(pd.concat([last_row[["ds", "qty", key_col]], new_row], ignore_index=True), key_col)

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

    items = fetch_invoice_items(store_id)
    if not items:
        raise ValueError("No invoice items found")

    products = supabase.table("products").select(
        "id, name, stock_quantity, reorder_level, unit, cost_price, selling_price, category_id, product_type"
    ).eq("store_id", store_id).eq("is_active", True).execute().data
    prod_by_id   = {p["id"]: p for p in products}
    prod_by_name = {p["name"]: p for p in products}
    cat_names    = fetch_categories(store_id)

    rows = []
    for item in items:
        if item.get("invoices") and item["invoices"].get("invoice_date"):
            pid = item.get("product_id")
            prod_info = prod_by_id.get(pid)
            category = cat_names.get(prod_info["category_id"], "Uncategorized") if prod_info and prod_info.get("category_id") else "Uncategorized"
            rows.append({
                "product_id":   pid,
                "product_name": item["product_name"],
                "category":     category,
                "quantity":     float(item["quantity"]),
                "date":         pd.to_datetime(item["invoices"]["invoice_date"]),
            })

    df = pd.DataFrame(rows)
    df = df[df["date"] >= (pd.Timestamp.now() - pd.Timedelta(days=395))]
    print(f"  {len(df)} rows, {df['product_name'].nunique()} unique products, {df['category'].nunique()} categories")

    df["week"] = df["date"].dt.to_period("W-MON").apply(lambda x: x.start_time)

    # Category-level weekly panel (this is what we train on)
    weekly_cat = df.groupby(["category", "week"])["quantity"].sum().reset_index()
    weekly_cat.columns = ["category", "ds", "qty"]
    weekly_cat["ds"] = pd.to_datetime(weekly_cat["ds"])

    category_list = sorted(df["category"].unique())
    print(f"\n  Building pooled panel across {len(category_list)} categories...")
    cat_panel = build_pooled_dataset(weekly_cat, category_list, "category")

    print(f"  Training pooled LightGBM model ({len(cat_panel)} category-weeks)...")
    model, metrics, categories = train_pooled_model(cat_panel, "category")

    print(f"\n  Generating 4-week forecasts per category...")
    category_forecasts = {}
    for cat in category_list:
        cat_hist = cat_panel[cat_panel["category"] == cat]
        preds = predict_next_weeks_group(model, categories, cat_hist, cat, "category", n_weeks=4)
        category_forecasts[cat] = {"predictions": preds, "total_4w": round(sum(preds), 1)}

    # Per-product weekly history (for shares + avg_weekly_qty, not modeled directly)
    weekly_prod = df.groupby(["product_name", "week"])["quantity"].sum().reset_index()
    weekly_prod.columns = ["product_name", "ds", "qty"]
    total_by_product = weekly_prod.groupby("product_name")["qty"].sum()
    total_by_category = df.groupby("category")["quantity"].sum()

    print(f"  Allocating category forecasts down to {len(prod_by_name)} products...")
    recommendations = []
    all_forecasts = {}

    for prod_name, prod_info in prod_by_name.items():
        category = cat_names.get(prod_info.get("category_id"), "Uncategorized") if prod_info.get("category_id") else "Uncategorized"
        cat_total = float(total_by_category.get(category, 0))
        prod_total = float(total_by_product.get(prod_name, 0))
        share = (prod_total / cat_total) if cat_total > 0 else 0.0

        cat_fc = category_forecasts.get(category, {"predictions": [0, 0, 0, 0], "total_4w": 0.0})
        predictions = [round(p * share, 2) for p in cat_fc["predictions"]]
        next_4w_demand = round(cat_fc["total_4w"] * share, 1)

        n_weeks_data = weekly_prod[weekly_prod["product_name"] == prod_name].shape[0]
        avg_weekly = (prod_total / n_weeks_data) if n_weeks_data > 0 else 0.0

        confidence = "high" if prod_info.get("product_type") == "fast" else "low"

        all_forecasts[prod_name] = {
            "method":         "category_pooled_lightgbm",
            "category":       category,
            "predictions":    predictions,
            "avg_weekly_qty": round(avg_weekly, 2),
            "confidence":     confidence,
        }

        current_stock  = float(prod_info.get("stock_quantity", 0))
        reorder_level  = float(prod_info.get("reorder_level", 5))
        unit           = prod_info.get("unit", "pcs")
        weeks_of_stock = current_stock / avg_weekly if avg_weekly > 0 else 99
        needs_restock  = current_stock <= reorder_level or weeks_of_stock < 2

        recommendations.append({
            "product_name":    prod_name,
            "category":        category,
            "unit":            unit,
            "current_stock":   current_stock,
            "reorder_level":   reorder_level,
            "avg_weekly_qty":  round(avg_weekly, 2),
            "next_4w_demand":  next_4w_demand,
            "weeks_of_stock":  round(weeks_of_stock, 1),
            "needs_restock":   bool(needs_restock),
            "suggested_order": round(max(0, next_4w_demand - current_stock + reorder_level), 1),
            "predictions":     predictions,
            "method":          "category_pooled_lightgbm",
            "confidence":      confidence,
        })

    recommendations.sort(key=lambda x: (not x["needs_restock"], x["weeks_of_stock"]))

    print("\n  Saving model...")
    joblib.dump(model, os.path.join(MODEL_DIR, f"inventory_category_model_{store_id}.pkl"))

    meta = {
        "model":              "lightgbm_category_pooled_tweedie",
        "version":            "3.0",
        "store_id":           store_id,
        "trained_on":         str(date.today()),
        "n_categories":       len(category_list),
        "n_products":         len(prod_by_name),
        "metrics":            metrics,
        "categories":         categories,
        "features":           FEATURE_COLS,
        "note":               "Forecast trained at category level (enough volume for a real signal) and allocated to products by historical sales share. Per-product 'confidence' reflects product_type (fast/slow mover) — low-confidence products rely primarily on the reorder_level threshold, not the demand forecast.",
    }
    with open(os.path.join(MODEL_DIR, f"inventory_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(MODEL_DIR, f"inventory_forecasts_{store_id}.json"), "w") as f:
        json.dump({"recommendations": recommendations, "forecasts": all_forecasts, "category_forecasts": category_forecasts}, f, indent=2)
    print("  Models saved.")

    urgent = [r for r in recommendations if r["needs_restock"]]
    print(f"\n  Restock Alerts ({len(urgent)} products need restocking):")
    print(f"  {'Product':<32} {'Cat':<18} {'Conf':<5} {'Stock':>6} {'4W Dem':>8} {'Order':>7}")
    print("  " + "-" * 80)
    for r in urgent[:12]:
        print(f"  {r['product_name'][:32]:<32} {r['category'][:18]:<18} {r['confidence']:<5} "
              f"{r['current_stock']:>6.0f} {r['next_4w_demand']:>8.1f} {r['suggested_order']:>7.1f} {r['unit']}")

    return model, recommendations


if __name__ == "__main__":
    train()
