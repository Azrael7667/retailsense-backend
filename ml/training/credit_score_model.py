import os
import json
import joblib
import pandas as pd
import numpy as np
from datetime import date
from dotenv import load_dotenv
from supabase import create_client
import lightgbm as lgb
import shap
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    roc_auc_score, precision_score,
    recall_score, f1_score, accuracy_score
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

CUTOFF_DAYS = 90  # outcome window = last 90 days; feature window = everything older


def fetch_all_pages(table, store_id, select="*"):
    all_rows = []
    page     = 0
    while True:
        q = supabase.table(table).select(select) \
            .eq("store_id", store_id) \
            .range(page * 1000, (page + 1) * 1000 - 1)
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
            .select("customer_id, invoice_date, total, status, payment_method, paid_amount") \
            .eq("store_id", store_id) \
            .range(page * 1000, (page + 1) * 1000 - 1) \
            .execute()
        all_inv.extend(result.data)
        if len(result.data) < 1000:
            break
        page += 1

    print("  Fetching khata entries...")
    khata = fetch_all_pages("khata_entries", store_id)

    print(f"  Customers: {len(customers)}, Invoices: {len(all_inv)}, Khata: {len(khata)}")
    return customers, all_inv, khata


def build_credit_features(customers, invoices, khata, cutoff_days=CUTOFF_DAYS):
    """
    Build credit scoring features using a TEMPORAL split to avoid label
    leakage. Features come ONLY from invoices before the cutoff date
    (the "history" window). The bad-credit label is computed from
    invoices AFTER the cutoff (the "outcome" window we're predicting)
    plus the customer's current balance/credit_limit — which can't be
    time-sliced since they're running totals, not per-invoice records,
    so they're excluded entirely from the model's input features
    instead (see FEATURE_COLS) even though they still define the label.

    Target: is_bad_credit (1 = risky, 0 = safe)
    """
    print("  Building credit features (temporal split)...")

    now = pd.Timestamp.now()
    cutoff_date = now - pd.Timedelta(days=cutoff_days)
    print(f"  Feature window: before {cutoff_date.date()}  |  Outcome window: {cutoff_date.date()} to {now.date()}")

    inv_df = pd.DataFrame(invoices)
    if not inv_df.empty:
        inv_df["invoice_date"] = pd.to_datetime(inv_df["invoice_date"])
        inv_df = inv_df[inv_df["invoice_date"] >= (now - pd.Timedelta(days=395))]

    khata_df = pd.DataFrame(khata)  # not yet time-split — empty for this store currently, see note below

    feature_rows = []
    for cust in customers:
        cid   = cust["id"]
        name  = cust.get("name", "")
        bal   = float(cust.get("balance", 0) or 0)
        limit = float(cust.get("credit_limit", 0) or 0)
        balance_ratio = min(bal / limit, 3.0) if limit > 0 else (1.0 if bal > 0 else 0.0)

        cust_inv_all = inv_df[inv_df["customer_id"] == cid] if not inv_df.empty else pd.DataFrame()
        cust_inv_feat    = cust_inv_all[cust_inv_all["invoice_date"] < cutoff_date] if not cust_inv_all.empty else pd.DataFrame()
        cust_inv_outcome = cust_inv_all[cust_inv_all["invoice_date"] >= cutoff_date] if not cust_inv_all.empty else pd.DataFrame()

        # --- FEATURES: from the EARLIER window only ---
        if cust_inv_feat.empty:
            n_purchases = 0
            total_spend = avg_purchase = max_purchase = 0.0
            credit_ratio = 0.0
            unpaid_ratio_feat = 0.0
            total_unpaid_amount = 0.0
            months_active = 0
        else:
            n_purchases  = len(cust_inv_feat)
            total_spend  = float(cust_inv_feat["total"].sum())
            avg_purchase = float(cust_inv_feat["total"].mean())
            max_purchase = float(cust_inv_feat["total"].max())

            credit_inv   = cust_inv_feat[cust_inv_feat["payment_method"] == "credit"]
            credit_ratio = len(credit_inv) / max(n_purchases, 1)

            unpaid_feat  = cust_inv_feat[cust_inv_feat["status"] == "unpaid"]
            unpaid_ratio_feat   = len(unpaid_feat) / max(n_purchases, 1)
            total_unpaid_amount = float(unpaid_feat["total"].sum()) if not unpaid_feat.empty else 0.0

            first = cust_inv_feat["invoice_date"].min()
            last  = cust_inv_feat["invoice_date"].max()
            months_active = max(1, (last - first).days // 30)

        # --- Khata: not yet time-split (no khata data yet for this store) ---
        cust_khata = khata_df[khata_df["party_id"] == cid] if not khata_df.empty else pd.DataFrame()
        n_debits  = 0
        n_credits = 0
        payback   = 0.0
        if not cust_khata.empty:
            n_debits  = int((cust_khata["entry_type"] == "debit").sum())
            n_credits = int((cust_khata["entry_type"] == "credit").sum())
            total_deb = float(cust_khata[cust_khata["entry_type"]=="debit"]["amount"].sum())
            total_cre = float(cust_khata[cust_khata["entry_type"]=="credit"]["amount"].sum())
            payback   = total_cre / max(total_deb, 1)

        # --- LABEL: from the LATER (outcome) window + current balance state ---
        if cust_inv_outcome.empty:
            unpaid_ratio_outcome = 0.0
        else:
            unpaid_outcome = cust_inv_outcome[cust_inv_outcome["status"] == "unpaid"]
            unpaid_ratio_outcome = len(unpaid_outcome) / max(len(cust_inv_outcome), 1)

        is_bad = int(
            balance_ratio > 0.8 or
            unpaid_ratio_outcome > 0.4 or
            (payback < 0.3 and n_debits > 3) or
            bal > 15000
        )

        feature_rows.append({
            "customer_id":          cid,
            "customer_name":        name,
            "balance":              bal,
            "credit_limit":         limit,
            "balance_ratio":        round(balance_ratio, 4),        # reporting only — NOT a model feature
            "n_purchases":          n_purchases,
            "total_spend":          total_spend,
            "avg_purchase":         avg_purchase,
            "max_purchase":         max_purchase,
            "credit_ratio":         credit_ratio,
            "unpaid_ratio":         unpaid_ratio_feat,               # from the EARLIER window — safe as a feature
            "total_unpaid_amount":  total_unpaid_amount,
            "months_active":        months_active,
            "n_khata_credits":      n_credits,
            "n_khata_debits":       n_debits,                        # reporting only — part of label formula
            "khata_payback_ratio":  round(payback, 4),               # reporting only — part of label formula
            "unpaid_ratio_outcome": round(unpaid_ratio_outcome, 4),  # reporting only — this IS the label basis
            "is_bad_credit":        is_bad,
        })

    df = pd.DataFrame(feature_rows)
    print(f"  Built features for {len(df)} customers")
    print(f"  Bad credit:  {df['is_bad_credit'].sum()} ({df['is_bad_credit'].mean()*100:.1f}%)")
    print(f"  Good credit: {(~df['is_bad_credit'].astype(bool)).sum()} ({(1-df['is_bad_credit'].mean())*100:.1f}%)")
    return df


FEATURE_COLS = [
    "n_purchases", "total_spend", "avg_purchase", "max_purchase",
    "credit_ratio", "unpaid_ratio", "total_unpaid_amount",
    "months_active", "n_khata_credits",
]


def train_logistic_baseline(X_train, X_test, y_train, y_test):
    print("\n  Training Logistic Regression baseline...")
    scaler   = StandardScaler()
    X_tr_s   = scaler.fit_transform(X_train)
    X_te_s   = scaler.transform(X_test)

    lr = LogisticRegression(
        class_weight = "balanced",
        max_iter     = 1000,
        random_state = 42,
    )
    lr.fit(X_tr_s, y_train)

    y_pred = lr.predict(X_te_s)
    y_prob = lr.predict_proba(X_te_s)[:, 1]

    metrics = {
        "auc":       round(float(roc_auc_score(y_test, y_prob)), 4) if y_test.nunique() > 1 else 0.5,
        "accuracy":  round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall":    round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1":        round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
    }
    print(f"  LR AUC: {metrics['auc']:.4f}  F1: {metrics['f1']:.4f}")
    return lr, scaler, metrics


def train_lgbm_model(X_train, X_test, y_train, y_test):
    print("\n  Training LightGBM credit model...")

    n_pos  = y_train.sum()
    n_neg  = len(y_train) - n_pos
    scale  = float(n_neg / max(n_pos, 1))

    model = lgb.LGBMClassifier(
        n_estimators      = 300,
        learning_rate     = 0.05,
        max_depth         = 4,
        num_leaves        = 15,
        min_child_samples = 3,
        subsample         = 0.8,
        colsample_bytree  = 0.8,
        scale_pos_weight  = scale,
        reg_alpha         = 0.1,
        reg_lambda        = 0.1,
        random_state      = 42,
        verbose           = -1,
    )
    model.fit(
        X_train, y_train,
        eval_set = [(X_test, y_test)],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(period=-1)],
    )

    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]

    metrics = {
        "auc":       round(float(roc_auc_score(y_test, y_prob)), 4) if y_test.nunique() > 1 else 0.5,
        "accuracy":  round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall":    round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1":        round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
    }
    print(f"  LGBM AUC: {metrics['auc']:.4f}  F1: {metrics['f1']:.4f}")
    return model, metrics


def compute_credit_scores(lgbm_model, df):
    print("\n  Computing credit scores with SHAP...")
    X          = df[FEATURE_COLS]
    proba_bad  = lgbm_model.predict_proba(X)[:, 1]
    scores     = np.round((1 - proba_bad) * 100).astype(int)

    explainer  = shap.TreeExplainer(lgbm_model)
    shap_vals  = explainer.shap_values(X)
    if isinstance(shap_vals, list):
        shap_vals = shap_vals[1]

    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        score      = int(scores[i])
        prob_bad   = float(proba_bad[i])

        if score >= 80:
            grade, decision, max_credit, color = "A", "Approve", 20000, "green"
        elif score >= 65:
            grade, decision, max_credit, color = "B", "Approve with caution", 10000, "blue"
        elif score >= 50:
            grade, decision, max_credit, color = "C", "Small credit only", 5000, "yellow"
        elif score >= 35:
            grade, decision, max_credit, color = "D", "Require advance payment", 2000, "orange"
        else:
            grade, decision, max_credit, color = "F", "Do not extend credit", 0, "red"

        shap_row    = pd.Series(shap_vals[i], index=FEATURE_COLS)
        top_factors = shap_row.abs().nlargest(3).index.tolist()
        explanations = []
        for feat in top_factors:
            val  = float(row[feat])
            sv   = float(shap_row[feat])
            direction = "negative" if sv > 0 else "positive"
            if feat == "unpaid_ratio":
                explanations.append({
                    "factor":    "Payment reliability (prior period)",
                    "value":     f"{val*100:.0f}% unpaid",
                    "impact":    direction,
                    "detail":    f"{val*100:.0f}% of invoices unpaid before the evaluation period"
                })
            elif feat == "n_purchases":
                explanations.append({
                    "factor":    "Purchase history",
                    "value":     f"{int(val)} purchases",
                    "impact":    direction,
                    "detail":    f"{int(val)} purchases on record prior to evaluation period"
                })
            elif feat == "credit_ratio":
                explanations.append({
                    "factor":    "Credit usage frequency",
                    "value":     f"{val*100:.0f}%",
                    "impact":    direction,
                    "detail":    f"Bought on credit {val*100:.0f}% of the time historically"
                })
            elif feat == "total_unpaid_amount":
                explanations.append({
                    "factor":    "Historical unpaid amount",
                    "value":     f"Rs {val:,.0f}",
                    "impact":    direction,
                    "detail":    f"Rs {val:,.0f} left unpaid during the observed history period"
                })
            elif feat == "months_active":
                explanations.append({
                    "factor":    "Account tenure",
                    "value":     f"{int(val)} months",
                    "impact":    direction,
                    "detail":    f"Active for {int(val)} months prior to evaluation"
                })
            elif feat == "n_khata_credits":
                explanations.append({
                    "factor":    "Udharo repayments made",
                    "value":     f"{int(val)} repayments",
                    "impact":    direction,
                    "detail":    f"{int(val)} khata credit (repayment) entries recorded"
                })
            else:
                explanations.append({
                    "factor":  feat.replace("_", " ").title(),
                    "value":   f"{val:.2f}",
                    "impact":  direction,
                    "detail":  f"{feat.replace('_',' ')} is {val:.2f}"
                })

        results.append({
            "customer_id":     str(row["customer_id"]),
            "customer_name":   row["customer_name"],
            "credit_score":    score,
            "grade":           grade,
            "decision":        decision,
            "max_recommended_credit": int(max_credit),
            "color":           color,
            "probability_bad": round(prob_bad, 4),
            "current_balance": round(float(row["balance"]), 2),
            "credit_limit":    round(float(row["credit_limit"]), 2),
            "balance_ratio":   round(float(row["balance_ratio"]), 4),
            "n_purchases":     int(row["n_purchases"]),
            "unpaid_ratio":    round(float(row["unpaid_ratio"]), 4),
            "explanations":    explanations,
        })

    results.sort(key=lambda x: x["credit_score"], reverse=True)
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

    print(f"\nTraining Credit Scoring Model for store: {store_id}")
    print("-" * 55)

    customers, invoices, khata = fetch_data(store_id)
    df = build_credit_features(customers, invoices, khata)

    X = df[FEATURE_COLS]
    y = df["is_bad_credit"]

    if y.nunique() < 2:
        print("  Only one class — adding synthetic bad credit cases")
        df.iloc[:5, df.columns.get_loc("is_bad_credit")] = 1
        y = df["is_bad_credit"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42,
        stratify=y if y.nunique() > 1 else None
    )

    lr_model, lr_scaler, lr_metrics = train_logistic_baseline(
        X_train, X_test, y_train, y_test
    )
    lgbm_model, lgbm_metrics = train_lgbm_model(
        X_train, X_test, y_train, y_test
    )

    print(f"\n  Model Comparison:")
    print(f"  {'Metric':<12} {'LR (Baseline)':>15} {'LightGBM':>15}")
    print("  " + "-" * 44)
    for m in ["auc", "accuracy", "precision", "recall", "f1"]:
        print(f"  {m:<12} {lr_metrics.get(m, 0):>15.4f} {lgbm_metrics.get(m, 0):>15.4f}")

    scores = compute_credit_scores(lgbm_model, df)

    print("\n  Saving models...")
    joblib.dump(lgbm_model, os.path.join(MODEL_DIR, f"credit_lgbm_{store_id}.pkl"))
    joblib.dump({"model": lr_model, "scaler": lr_scaler},
                os.path.join(MODEL_DIR, f"credit_lr_{store_id}.pkl"))

    meta = {
        "model":          "lgbm_with_lr_baseline_temporal_split",
        "version":        "2.0",
        "store_id":       store_id,
        "trained_on":     str(date.today()),
        "cutoff_days":    CUTOFF_DAYS,
        "lgbm_metrics":   lgbm_metrics,
        "lr_metrics":     lr_metrics,
        "features":       FEATURE_COLS,
        "n_customers":    len(df),
        "n_bad_credit":   int(y.sum()),
        "note":           "Features computed from invoices older than cutoff_days; label (is_bad_credit) computed from invoices within the last cutoff_days plus current balance/credit_limit. This temporal split prevents the label-leakage issue present in v1.0, where features and label were both derived from the same current-state variables.",
    }
    with open(os.path.join(MODEL_DIR, f"credit_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(MODEL_DIR, f"credit_scores_{store_id}.json"), "w") as f:
        json.dump({"scores": scores}, f, indent=2)

    print("  Models saved.")

    print(f"\n  Credit Score Summary:")
    grade_counts = {}
    for s in scores:
        g = s["grade"]
        grade_counts[g] = grade_counts.get(g, 0) + 1

    for grade in ["A", "B", "C", "D", "F"]:
        count = grade_counts.get(grade, 0)
        label = {"A":"Excellent","B":"Good","C":"Fair","D":"Poor","F":"Very High Risk"}[grade]
        print(f"  Grade {grade} ({label}): {count} customers")

    print(f"\n  Top 5 by Credit Score:")
    print(f"  {'Customer':<30} {'Score':>6} {'Grade':>6} {'Decision'}")
    print("  " + "-" * 70)
    for s in scores[:5]:
        print(f"  {s['customer_name']:<30} {s['credit_score']:>6} "
              f"{s['grade']:>6}  {s['decision']}")

    print(f"\n  Bottom 5 (High Risk):")
    for s in scores[-5:]:
        print(f"  {s['customer_name']:<30} {s['credit_score']:>6} "
              f"{s['grade']:>6}  {s['decision']}")

    return lgbm_model, scores


if __name__ == "__main__":
    train()
