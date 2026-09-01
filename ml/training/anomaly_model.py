

import os
import json
import joblib
import pandas as pd
import numpy as np
from datetime import date
from dotenv import load_dotenv
from supabase import create_client
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import warnings
warnings.filterwarnings("ignore")

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
            .select("id, invoice_date, total, subtotal, discount, paid_amount, payment_method, status, customer_id") \
            .eq("store_id", store_id) \
            .range(page * 1000, (page + 1) * 1000 - 1) \
            .execute()
        all_inv.extend(result.data)
        if len(result.data) < 1000:
            break
        page += 1

    df = pd.DataFrame(all_inv)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df = df[df["invoice_date"] >= (pd.Timestamp.now() - pd.Timedelta(days=395))]
    print(f"  {len(df)} invoices loaded")
    return df


def build_features(df):
    """Build anomaly detection features"""
    feat = pd.DataFrame()

    feat["total"]           = df["total"].astype(float)
    feat["subtotal"]        = df["subtotal"].astype(float)
    feat["discount"]        = df["discount"].astype(float)
    feat["discount_ratio"]  = feat["discount"] / feat["subtotal"].replace(0, 1)
    feat["day_of_week"]     = df["invoice_date"].dt.dayofweek
    feat["month"]           = df["invoice_date"].dt.month
    feat["is_credit"]       = (df["payment_method"] == "credit").astype(int)
    feat["is_unpaid"]       = (df["status"] == "unpaid").astype(int)
    feat["has_customer"]    = df["customer_id"].notna().astype(int)
    feat["paid_ratio"]      = df["paid_amount"].astype(float) / df["total"].astype(float).replace(0, 1)

    # Log transform for skewed distributions
    feat["log_total"]       = np.log1p(feat["total"])
    feat["log_discount"]    = np.log1p(feat["discount"])

    return feat.fillna(0)


FEATURE_COLS = [
    "log_total", "discount_ratio", "day_of_week",
    "month", "is_credit", "is_unpaid", "paid_ratio", "log_discount",
]


def train_anomaly_model(feat_df, contamination=0.03):
    """Train Isolation Forest — 3% contamination = ~3% of transactions flagged"""
    print(f"  Training Isolation Forest (contamination={contamination})...")

    X      = feat_df[FEATURE_COLS]
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = IsolationForest(
        n_estimators  = 200,
        max_samples   = "auto",
        contamination = contamination,
        max_features  = 1.0,
        random_state  = 42,
        n_jobs        = -1,
    )
    model.fit(X_scaled)

    scores   = model.score_samples(X_scaled)   # More negative = more anomalous
    preds    = model.predict(X_scaled)          # -1 = anomaly, 1 = normal
    n_anomalies = int((preds == -1).sum())
    print(f"  Detected {n_anomalies} anomalies ({n_anomalies/len(preds)*100:.1f}%)")

    return model, scaler, scores, preds


def build_anomaly_report(df, feat_df, scores, preds):
    """Build detailed anomaly report"""
    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        is_anomaly    = bool(preds[i] == -1)
        anomaly_score = float(scores[i])
        # Normalize score to 0-100 (higher = more anomalous)
        severity      = max(0, min(100, int((-anomaly_score + 0.5) * 100)))

        reasons = []
        feat_row = feat_df.iloc[i]

        # Determine why it's anomalous
        if feat_row["log_total"] > feat_df["log_total"].quantile(0.97):
            reasons.append(f"Unusually large transaction: Rs {row['total']:,.0f}")
        if feat_row["discount_ratio"] > 0.3:
            reasons.append(f"High discount ratio: {feat_row['discount_ratio']*100:.0f}%")
        if feat_row["is_unpaid"] and feat_row["log_total"] > feat_df["log_total"].quantile(0.8):
            reasons.append("Large unpaid transaction")
        if feat_row["day_of_week"] == 6:
            reasons.append("Transaction on Sunday (store usually closed)")
        if not reasons and is_anomaly:
            reasons.append("Unusual combination of transaction patterns")

        results.append({
            "invoice_id":     str(row["id"]),
            "invoice_date":   str(row["invoice_date"].date()),
            "total":          round(float(row["total"]), 2),
            "payment_method": row["payment_method"],
            "status":         row["status"],
            "is_anomaly":     is_anomaly,
            "anomaly_score":  round(anomaly_score, 4),
            "severity":       severity,
            "reasons":        reasons if is_anomaly else [],
        })

    # Sort by severity
    results.sort(key=lambda x: x["anomaly_score"])
    return results


def get_store_id():
    result = supabase.table("stores").select("id") \
        .eq("name", "Bijeta Auto Parts").single().execute()
    if not result.data:
        raise ValueError("Store not found")
    return result.data["id"]


def train(store_id=None):
    if not store_id:
        store_id = get_store_id()

    print(f"\nTraining Anomaly Detection Model for store: {store_id}")
    print("-" * 55)

    df       = fetch_data(store_id)
    feat_df  = build_features(df)
    model, scaler, scores, preds = train_anomaly_model(feat_df)
    results  = build_anomaly_report(df, feat_df, scores, preds)

    # Save
    joblib.dump({"model": model, "scaler": scaler}, 
                os.path.join(MODEL_DIR, f"anomaly_model_{store_id}.pkl"))

    anomalies = [r for r in results if r["is_anomaly"]]
    meta = {
        "model":        "isolation_forest",
        "version":      "1.0",
        "store_id":     store_id,
        "trained_on":   str(date.today()),
        "n_transactions": len(df),
        "n_anomalies":  len(anomalies),
        "anomaly_rate": round(len(anomalies)/len(df)*100, 2),
        "features":     FEATURE_COLS,
    }
    with open(os.path.join(MODEL_DIR, f"anomaly_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(MODEL_DIR, f"anomaly_results_{store_id}.json"), "w") as f:
        json.dump({"results": results}, f, indent=2)

    print("\n  Top Anomalies Detected:")
    print(f"  {'Date':<12} {'Amount':>12} {'Method':<12} {'Reason'}")
    print("  " + "-" * 65)
    for r in anomalies[:8]:
        reason = r["reasons"][0] if r["reasons"] else "Unusual pattern"
        print(f"  {r['invoice_date']:<12} Rs {r['total']:>9,.0f} "
              f"{r['payment_method']:<12} {reason}")

    return model, results


if __name__ == "__main__":
    train()
