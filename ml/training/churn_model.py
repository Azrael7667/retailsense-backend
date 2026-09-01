

import os
import json
import joblib
import pandas as pd
import numpy as np
from datetime import date, timedelta
from dotenv import load_dotenv
from supabase import create_client
import lightgbm as lgb
import shap
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    classification_report, roc_auc_score,
    precision_score, recall_score, f1_score
)
import warnings
warnings.filterwarnings("ignore")

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models_saved")
os.makedirs(MODEL_DIR, exist_ok=True)

CHURN_DAYS = 60  # Customer inactive for 60 days = churned


def fetch_all_pages(table, store_id, select="*", extra_filters=None):
    all_rows = []
    page     = 0
    while True:
        q = supabase.table(table).select(select) \
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


def fetch_data(store_id):
    print("  Fetching customers...")
    customers = fetch_all_pages("customers", store_id)

    print("  Fetching invoices (paginated)...")
    all_inv = []
    page    = 0
    while True:
        result = supabase.table("invoices") \
            .select("id, customer_id, invoice_date, total, status, payment_method") \
            .eq("store_id", store_id) \
            .range(page * 1000, (page + 1) * 1000 - 1) \
            .execute()
        all_inv.extend(result.data)
        if len(result.data) < 1000:
            break
        page += 1

    print(f"  Customers: {len(customers)}, Invoices: {len(all_inv)}")
    return customers, all_inv


def build_customer_features(customers, invoices):
    """
    Build RFM + behavioral features per customer
    R = Recency  (days since last purchase)
    F = Frequency (number of purchases)
    M = Monetary  (total spend)
    """
    print("  Building customer features...")

    inv_df = pd.DataFrame(invoices)
    inv_df["invoice_date"] = pd.to_datetime(inv_df["invoice_date"])

    # Use 2024 data only for clean training
    inv_df = inv_df[inv_df["invoice_date"] >= (pd.Timestamp.now() - pd.Timedelta(days=395))]

    # Reference date = today (was hardcoded to end of 2024 — broke once real data moved past that)
    ref_date = pd.Timestamp.now().normalize()

    feature_rows = []
    for cust in customers:
        cid        = cust["id"]
        cust_invs  = inv_df[inv_df["customer_id"] == cid]

        if cust_invs.empty:
            continue

        last_purchase   = cust_invs["invoice_date"].max()
        first_purchase  = cust_invs["invoice_date"].min()
        recency_days    = (ref_date - last_purchase).days
        frequency       = len(cust_invs)
        monetary_total  = float(cust_invs["total"].sum())
        monetary_avg    = float(cust_invs["total"].mean())
        monetary_max    = float(cust_invs["total"].max())

        # Time between purchases
        if frequency > 1:
            dates   = cust_invs["invoice_date"].sort_values()
            gaps    = dates.diff().dt.days.dropna()
            avg_gap = float(gaps.mean())
            std_gap = float(gaps.std()) if len(gaps) > 1 else 0.0
        else:
            avg_gap = 999.0
            std_gap = 0.0

        # Payment behavior
        credit_ratio = float(
            (cust_invs["payment_method"] == "credit").sum() / frequency
        )
        unpaid_ratio = float(
            (cust_invs["status"] == "unpaid").sum() / frequency
        )

        # Customer balance (udharo)
        balance      = float(cust.get("balance", 0) or 0)
        credit_limit = float(cust.get("credit_limit", 0) or 0)
        balance_ratio = balance / credit_limit if credit_limit > 0 else 0.0

        # Days active
        days_active  = (last_purchase - first_purchase).days + 1
        purchase_rate = frequency / max(days_active, 1) * 30  # per month

        # Churn label: no purchase in last 60 days of training period
        # We look at purchases in last 2 months vs first 10 months
        late_period_start = ref_date - pd.Timedelta(days=60)  # last ~2 months, relative to today
        recent_purchases  = len(cust_invs[cust_invs["invoice_date"] >= late_period_start])
        is_churned        = int(recency_days >= CHURN_DAYS)

        feature_rows.append({
            "customer_id":    cid,
            "customer_name":  cust.get("name", ""),
            "recency_days":   recency_days,
            "frequency":      frequency,
            "monetary_total": monetary_total,
            "monetary_avg":   monetary_avg,
            "monetary_max":   monetary_max,
            "avg_gap_days":   avg_gap,
            "std_gap_days":   std_gap,
            "credit_ratio":   credit_ratio,
            "unpaid_ratio":   unpaid_ratio,
            "balance":        balance,
            "balance_ratio":  min(balance_ratio, 2.0),
            "days_active":    days_active,
            "purchase_rate":  purchase_rate,
            "recent_purchases": recent_purchases,
            "is_churned":     is_churned,
        })

    df = pd.DataFrame(feature_rows)
    print(f"  Built features for {len(df)} customers")
    print(f"  Churned: {df['is_churned'].sum()} ({df['is_churned'].mean()*100:.1f}%)")
    print(f"  Active:  {(~df['is_churned'].astype(bool)).sum()} ({(1-df['is_churned'].mean())*100:.1f}%)")
    return df


FEATURE_COLS = [
    "recency_days", "frequency", "monetary_total", "monetary_avg",
    "monetary_max", "avg_gap_days", "std_gap_days", "credit_ratio",
    "unpaid_ratio", "balance", "balance_ratio", "days_active",
    "purchase_rate", "recent_purchases",
]


def train_churn_model(df):
    print("\n  Training LightGBM churn model...")

    X = df[FEATURE_COLS]
    y = df["is_churned"]

    # Class weights to handle imbalance
    n_pos = y.sum()
    n_neg = len(y) - n_pos
    scale = n_neg / max(n_pos, 1)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y if y.nunique() > 1 else None
    )

    model = lgb.LGBMClassifier(
        n_estimators      = 300,
        learning_rate     = 0.05,
        max_depth         = 4,
        num_leaves        = 15,
        min_child_samples = 3,
        subsample         = 0.8,
        colsample_bytree  = 0.8,
        scale_pos_weight  = float(scale),
        reg_alpha         = 0.1,
        reg_lambda        = 0.1,
        random_state      = 42,
        verbose           = -1,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(period=-1)],
    )

    # Evaluate
    y_pred      = model.predict(X_test)
    y_pred_prob = model.predict_proba(X_test)[:, 1]

    metrics = {
        "auc":       round(float(roc_auc_score(y_test, y_pred_prob)), 4) if y_test.nunique() > 1 else 0.5,
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall":    round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1":        round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "churn_rate": round(float(y.mean()), 4),
        "n_customers": int(len(df)),
        "n_churned":   int(y.sum()),
    }

    print(f"  AUC:       {metrics['auc']:.4f}")
    print(f"  Precision: {metrics['precision']:.4f}")
    print(f"  Recall:    {metrics['recall']:.4f}")
    print(f"  F1:        {metrics['f1']:.4f}")
    return model, metrics


def compute_shap(model, df):
    """Compute SHAP values for all customers"""
    print("\n  Computing SHAP values...")
    X          = df[FEATURE_COLS]
    explainer  = shap.TreeExplainer(model)
    shap_vals  = explainer.shap_values(X)

    # For binary classification LightGBM
    if isinstance(shap_vals, list):
        shap_vals = shap_vals[1]

    shap_df = pd.DataFrame(shap_vals, columns=FEATURE_COLS)
    print("  SHAP values computed.")
    return shap_df


def build_predictions(model, df, shap_df):
    """Build per-customer churn predictions with explanations"""
    X          = df[FEATURE_COLS]
    proba      = model.predict_proba(X)[:, 1]
    predictions = []

    for i, (_, row) in enumerate(df.iterrows()):
        churn_prob = float(proba[i])

        # Risk level
        if churn_prob >= 0.7:
            risk = "high"
        elif churn_prob >= 0.4:
            risk = "medium"
        else:
            risk = "low"

        # Top SHAP factors
        shap_row  = shap_df.iloc[i]
        top_factors = shap_row.abs().nlargest(3).index.tolist()
        explanations = []
        for feat in top_factors:
            val      = float(row[feat])
            shap_val = float(shap_row[feat])
            direction = "increases" if shap_val > 0 else "decreases"
            if feat == "recency_days":
                explanations.append(f"Last purchase {int(val)} days ago {direction} churn risk")
            elif feat == "frequency":
                explanations.append(f"Only {int(val)} total purchases {direction} churn risk")
            elif feat == "avg_gap_days":
                explanations.append(f"Average {int(val)} days between purchases {direction} churn risk")
            elif feat == "monetary_total":
                explanations.append(f"Total spend Rs {val:,.0f} {direction} churn risk")
            elif feat == "credit_ratio":
                explanations.append(f"Credit payment ratio {val:.0%} {direction} churn risk")
            elif feat == "unpaid_ratio":
                explanations.append(f"Unpaid ratio {val:.0%} {direction} churn risk")
            elif feat == "balance":
                explanations.append(f"Outstanding balance Rs {val:,.0f} {direction} churn risk")
            elif feat == "recent_purchases":
                explanations.append(f"Only {int(val)} recent purchases {direction} churn risk")
            elif feat == "purchase_rate":
                explanations.append(f"Purchase rate {val:.1f}/month {direction} churn risk")
            else:
                explanations.append(f"{feat.replace('_',' ').title()}: {val:.2f} {direction} churn risk")

        # Action recommendation
        if risk == "high":
            action = "Call customer immediately. Offer special discount or loyalty reward."
        elif risk == "medium":
            action = "Send reminder message. Check if they need any parts."
        else:
            action = "Customer is active. Continue regular engagement."

        predictions.append({
            "customer_id":    str(row["customer_id"]),
            "customer_name":  row["customer_name"],
            "churn_probability": round(churn_prob, 4),
            "churn_percent":  round(churn_prob * 100, 1),
            "risk_level":     risk,
            "is_churned":     bool(row["is_churned"]),
            "recency_days":   int(row["recency_days"]),
            "frequency":      int(row["frequency"]),
            "monetary_total": round(float(row["monetary_total"]), 2),
            "last_purchase_days_ago": int(row["recency_days"]),
            "explanations":   explanations,
            "action":         action,
        })

    predictions.sort(key=lambda x: x["churn_probability"], reverse=True)
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

    print(f"\nTraining Customer Churn Model for store: {store_id}")
    print(f"Churn definition: No purchase in {CHURN_DAYS} days")
    print("-" * 55)

    customers, invoices = fetch_data(store_id)
    df                  = build_customer_features(customers, invoices)

    if df.empty:
        raise ValueError("No customer data found")

    if df["is_churned"].nunique() < 2:
        print("  WARNING: All customers have same churn status. Adding synthetic variance.")
        # Force some churn for demo
        df.iloc[:len(df)//3, df.columns.get_loc("is_churned")] = 1

    model, metrics = train_churn_model(df)
    shap_df        = compute_shap(model, df)
    predictions    = build_predictions(model, df, shap_df)

    # Save
    print("\n  Saving model...")
    joblib.dump(model, os.path.join(MODEL_DIR, f"churn_model_{store_id}.pkl"))

    meta = {
        "model":       "lightgbm_classifier",
        "version":     "1.0",
        "store_id":    store_id,
        "trained_on":  str(date.today()),
        "churn_days":  CHURN_DAYS,
        "metrics":     metrics,
        "features":    FEATURE_COLS,
    }
    with open(os.path.join(MODEL_DIR, f"churn_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)

    with open(os.path.join(MODEL_DIR, f"churn_predictions_{store_id}.json"), "w") as f:
        json.dump({"predictions": predictions}, f, indent=2)

    print("  Model saved.")

    # Print results
    high_risk   = [p for p in predictions if p["risk_level"] == "high"]
    medium_risk = [p for p in predictions if p["risk_level"] == "medium"]
    low_risk    = [p for p in predictions if p["risk_level"] == "low"]

    print(f"\n  Churn Risk Summary:")
    print(f"  High risk:   {len(high_risk)} customers")
    print(f"  Medium risk: {len(medium_risk)} customers")
    print(f"  Low risk:    {len(low_risk)} customers")

    print(f"\n  Top 5 High-Risk Customers:")
    print(f"  {'Customer':<30} {'Churn %':>8} {'Last Purchase':>14} {'Action'}")
    print("  " + "-" * 80)
    for p in predictions[:5]:
        print(f"  {p['customer_name']:<30} "
              f"{p['churn_percent']:>7.1f}% "
              f"{p['recency_days']:>10} days ago")
        for exp in p["explanations"][:2]:
            print(f"    → {exp}")
        print(f"    ✓ {p['action']}")
        print()

    return model, predictions


if __name__ == "__main__":
    train()
