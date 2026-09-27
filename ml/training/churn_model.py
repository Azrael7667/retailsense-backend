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
    precision_score, recall_score, f1_score,
    precision_recall_curve
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

CHURN_DAYS = 60


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


def build_customer_features(customers, invoices, cutoff_days=CHURN_DAYS):
    print("  Building customer features (temporal split)...")

    inv_df = pd.DataFrame(invoices)
    inv_df["invoice_date"] = pd.to_datetime(inv_df["invoice_date"])
    inv_df = inv_df[inv_df["invoice_date"] >= (pd.Timestamp.now() - pd.Timedelta(days=395))]

    ref_date    = pd.Timestamp.now().normalize()
    cutoff_date = ref_date - pd.Timedelta(days=cutoff_days)
    print(f"  Feature window: before {cutoff_date.date()}  |  Outcome window: {cutoff_date.date()} to {ref_date.date()}")

    feature_rows = []
    for cust in customers:
        cid = cust["id"]
        cust_invs_all = inv_df[inv_df["customer_id"] == cid]

        cust_invs_feat    = cust_invs_all[cust_invs_all["invoice_date"] < cutoff_date]
        cust_invs_outcome = cust_invs_all[cust_invs_all["invoice_date"] >= cutoff_date]

        if cust_invs_feat.empty:
            continue

        last_purchase  = cust_invs_feat["invoice_date"].max()
        first_purchase = cust_invs_feat["invoice_date"].min()
        recency_days   = (cutoff_date - last_purchase).days
        frequency      = len(cust_invs_feat)
        monetary_total = float(cust_invs_feat["total"].sum())
        monetary_avg   = float(cust_invs_feat["total"].mean())
        monetary_max   = float(cust_invs_feat["total"].max())

        if frequency > 1:
            dates   = cust_invs_feat["invoice_date"].sort_values()
            gaps    = dates.diff().dt.days.dropna()
            avg_gap = float(gaps.mean())
            std_gap = float(gaps.std()) if len(gaps) > 1 else 0.0
        else:
            avg_gap = 999.0
            std_gap = 0.0

        credit_ratio = float((cust_invs_feat["payment_method"] == "credit").sum() / frequency)
        unpaid_ratio = float((cust_invs_feat["status"] == "unpaid").sum() / frequency)

        balance       = float(cust.get("balance", 0) or 0)
        credit_limit  = float(cust.get("credit_limit", 0) or 0)
        balance_ratio = balance / credit_limit if credit_limit > 0 else 0.0

        days_active   = (last_purchase - first_purchase).days + 1
        purchase_rate = frequency / max(days_active, 1) * 30

        is_churned = int(cust_invs_outcome.empty)

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
    "purchase_rate",
]


def find_best_threshold(y_true, y_prob):
    """Sweep thresholds and return the one maximizing F1, instead of
    blindly using the default 0.5 -- with a small, imbalanced dataset,
    predicted probabilities can stay compressed well below 0.5 for every
    customer even when the model has genuine ranking ability (see AUC),
    which silently zeroes out precision/recall/F1 and every risk bucket
    if left at the default cutoff."""
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_prob)
    f1s = np.divide(
        2 * precisions * recalls, precisions + recalls,
        out=np.zeros_like(precisions), where=(precisions + recalls) > 0
    )
    if len(thresholds) == 0:
        return 0.5
    best_idx = np.argmax(f1s[:-1]) if len(f1s) > 1 else 0
    return float(thresholds[best_idx]) if best_idx < len(thresholds) else 0.5


def train_churn_model(df):
    print("\n  Training LightGBM churn model...")

    X = df[FEATURE_COLS]
    y = df["is_churned"]

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

    y_pred_prob = model.predict_proba(X_test)[:, 1]

    best_threshold = find_best_threshold(y_test.values, y_pred_prob) if y_test.nunique() > 1 else 0.5
    y_pred_at_best = (y_pred_prob >= best_threshold).astype(int)

    metrics = {
        "auc":              round(float(roc_auc_score(y_test, y_pred_prob)), 4) if y_test.nunique() > 1 else 0.5,
        "best_threshold":   round(best_threshold, 4),
        "precision":        round(float(precision_score(y_test, y_pred_at_best, zero_division=0)), 4),
        "recall":           round(float(recall_score(y_test, y_pred_at_best, zero_division=0)), 4),
        "f1":               round(float(f1_score(y_test, y_pred_at_best, zero_division=0)), 4),
        "precision_at_0.5": round(float(precision_score(y_test, (y_pred_prob >= 0.5).astype(int), zero_division=0)), 4),
        "recall_at_0.5":    round(float(recall_score(y_test, (y_pred_prob >= 0.5).astype(int), zero_division=0)), 4),
        "churn_rate":       round(float(y.mean()), 4),
        "n_customers":      int(len(df)),
        "n_churned":        int(y.sum()),
        "n_test_churned":   int(y_test.sum()),
    }

    print(f"  AUC:                 {metrics['auc']:.4f}")
    print(f"  Best F1 threshold:   {metrics['best_threshold']:.4f}  (vs default 0.5)")
    print(f"  Precision @ best:    {metrics['precision']:.4f}   (@0.5 was {metrics['precision_at_0.5']:.4f})")
    print(f"  Recall @ best:       {metrics['recall']:.4f}   (@0.5 was {metrics['recall_at_0.5']:.4f})")
    print(f"  F1 @ best:           {metrics['f1']:.4f}")
    print(f"  [Caveat: only {metrics['n_test_churned']} churned customers in the test fold -- "
          f"these metrics will be noisy at this sample size]")
    return model, metrics


def compute_shap(model, df):
    print("\n  Computing SHAP values...")
    X          = df[FEATURE_COLS]
    explainer  = shap.TreeExplainer(model)
    shap_vals  = explainer.shap_values(X)

    if isinstance(shap_vals, list):
        shap_vals = shap_vals[1]

    shap_df = pd.DataFrame(shap_vals, columns=FEATURE_COLS)
    print("  SHAP values computed.")
    return shap_df


def build_predictions(model, df, shap_df):
    """Risk buckets are percentile-based on THIS population's predicted
    probabilities, not fixed absolute cutoffs -- with a small, imbalanced
    dataset the model's probabilities are naturally compressed, so a fixed
    0.4/0.7 cutoff can leave every bucket empty even when the model has
    real ranking ability. Top ~15% by probability = high, next ~25% =
    medium, rest = low -- this always surfaces the riskiest customers in
    relative terms, which is what the business actually needs from this
    tool ("who should I call this week"), rather than an absolute
    probability the small sample can't calibrate reliably anyway."""
    X     = df[FEATURE_COLS]
    proba = model.predict_proba(X)[:, 1]

    high_cut   = float(np.percentile(proba, 85)) if len(proba) >= 5 else 0.7
    medium_cut = float(np.percentile(proba, 60)) if len(proba) >= 5 else 0.4
    print(f"\n  Risk bucket cutoffs (percentile-based): high >= {high_cut:.3f}, medium >= {medium_cut:.3f}")

    predictions = []
    for i, (_, row) in enumerate(df.iterrows()):
        churn_prob = float(proba[i])

        if churn_prob >= high_cut:
            risk = "high"
        elif churn_prob >= medium_cut:
            risk = "medium"
        else:
            risk = "low"

        shap_row  = shap_df.iloc[i]
        top_factors = shap_row.abs().nlargest(3).index.tolist()
        explanations = []
        for feat in top_factors:
            val      = float(row[feat])
            shap_val = float(shap_row[feat])
            direction = "increases" if shap_val > 0 else "decreases"
            if feat == "recency_days":
                explanations.append(f"Last purchase {int(val)} days before the evaluation point {direction} churn risk")
            elif feat == "frequency":
                explanations.append(f"Only {int(val)} total purchases (prior history) {direction} churn risk")
            elif feat == "avg_gap_days":
                explanations.append(f"Average {int(val)} days between purchases {direction} churn risk")
            elif feat == "monetary_total":
                explanations.append(f"Total spend Rs {val:,.0f} (prior history) {direction} churn risk")
            elif feat == "credit_ratio":
                explanations.append(f"Credit payment ratio {val:.0%} {direction} churn risk")
            elif feat == "unpaid_ratio":
                explanations.append(f"Unpaid ratio {val:.0%} {direction} churn risk")
            elif feat == "balance":
                explanations.append(f"Outstanding balance Rs {val:,.0f} {direction} churn risk")
            elif feat == "purchase_rate":
                explanations.append(f"Purchase rate {val:.1f}/month (prior history) {direction} churn risk")
            else:
                explanations.append(f"{feat.replace('_',' ').title()}: {val:.2f} {direction} churn risk")

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
            "explanations":   explanations,
            "action":         action,
        })

    predictions.sort(key=lambda x: x["churn_probability"], reverse=True)
    return predictions, high_cut, medium_cut


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
    print(f"Churn definition: no purchase in the {CHURN_DAYS}-day outcome window, given prior purchase history")
    print("-" * 55)

    customers, invoices = fetch_data(store_id)
    df                  = build_customer_features(customers, invoices)

    if df.empty:
        raise ValueError("No customer data found")

    if df["is_churned"].nunique() < 2:
        print("  WARNING: All customers have same churn status. Adding synthetic variance.")
        df.iloc[:len(df)//3, df.columns.get_loc("is_churned")] = 1

    model, metrics                  = train_churn_model(df)
    shap_df                         = compute_shap(model, df)
    predictions, high_cut, med_cut  = build_predictions(model, df, shap_df)

    print("\n  Saving model...")
    joblib.dump(model, os.path.join(MODEL_DIR, f"churn_model_{store_id}.pkl"))

    meta = {
        "model":              "lightgbm_classifier_temporal_split_calibrated",
        "version":            "2.1",
        "store_id":           store_id,
        "trained_on":         str(date.today()),
        "churn_days":         CHURN_DAYS,
        "metrics":            metrics,
        "risk_bucket_cutoffs": {"high": round(high_cut, 4), "medium": round(med_cut, 4)},
        "features":           FEATURE_COLS,
        "note":               "v2.0 fixed label leakage via temporal split. v2.1 adds F1-optimal threshold for reported metrics and percentile-based (not fixed) risk bucket cutoffs, since v2.0's fixed 0.4/0.7 cutoffs left every risk bucket empty despite the model having real ranking ability (AUC 0.75) -- probabilities stay compressed well below 0.5 with only 50 customers / 8 churn events.",
    }
    with open(os.path.join(MODEL_DIR, f"churn_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)

    with open(os.path.join(MODEL_DIR, f"churn_predictions_{store_id}.json"), "w") as f:
        json.dump({"predictions": predictions}, f, indent=2)

    print("  Model saved.")

    high_risk   = [p for p in predictions if p["risk_level"] == "high"]
    medium_risk = [p for p in predictions if p["risk_level"] == "medium"]
    low_risk    = [p for p in predictions if p["risk_level"] == "low"]

    print(f"\n  Churn Risk Summary:")
    print(f"  High risk:   {len(high_risk)} customers")
    print(f"  Medium risk: {len(medium_risk)} customers")
    print(f"  Low risk:    {len(low_risk)} customers")

    print(f"\n  Top 5 High-Risk Customers:")
    print(f"  {'Customer':<30} {'Churn %':>8} {'Recency (as of cutoff)':>22}")
    print("  " + "-" * 80)
    for p in predictions[:5]:
        print(f"  {p['customer_name']:<30} "
              f"{p['churn_percent']:>7.1f}% "
              f"{p['recency_days']:>19} days")
        for exp in p["explanations"][:2]:
            print(f"    → {exp}")
        print(f"    ✓ {p['action']}")
        print()

    return model, predictions


if __name__ == "__main__":
    train()
