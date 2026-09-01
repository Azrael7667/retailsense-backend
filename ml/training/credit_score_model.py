
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


def build_credit_features(customers, invoices, khata):
    """
    Build credit scoring features per customer
    Target: is_bad_credit (1 = risky, 0 = safe)
    """
    print("  Building credit features...")

    inv_df = pd.DataFrame(invoices)
    if not inv_df.empty:
        inv_df["invoice_date"] = pd.to_datetime(inv_df["invoice_date"])
        inv_df = inv_df[inv_df["invoice_date"] >= (pd.Timestamp.now() - pd.Timedelta(days=395))]

    khata_df = pd.DataFrame(khata)

    feature_rows = []
    for cust in customers:
        cid   = cust["id"]
        name  = cust.get("name", "")
        bal   = float(cust.get("balance", 0) or 0)
        limit = float(cust.get("credit_limit", 0) or 0)

        # Invoice features
        cust_inv = inv_df[inv_df["customer_id"] == cid] if not inv_df.empty else pd.DataFrame()

        if cust_inv.empty:
            # No invoice history — neutral credit
            feature_rows.append({
                "customer_id":         cid,
                "customer_name":       name,
                "balance":             bal,
                "credit_limit":        limit,
                "n_purchases":         0,
                "total_spend":         0.0,
                "avg_purchase":        0.0,
                "max_purchase":        0.0,
                "n_credit_purchases":  0,
                "credit_ratio":        0.0,
                "n_unpaid":            0,
                "unpaid_ratio":        0.0,
                "total_unpaid_amount": 0.0,
                "avg_days_to_pay":     999.0,
                "balance_ratio":       bal / limit if limit > 0 else 0.0,
                "n_khata_debits":      0,
                "n_khata_credits":     0,
                "khata_payback_ratio": 0.0,
                "months_active":       0,
                "is_bad_credit":       int(bal > limit * 0.8) if limit > 0 else 0,
            })
            continue

        n_purchases  = len(cust_inv)
        total_spend  = float(cust_inv["total"].sum())
        avg_purchase = float(cust_inv["total"].mean())
        max_purchase = float(cust_inv["total"].max())

        credit_inv   = cust_inv[cust_inv["payment_method"] == "credit"]
        n_credit     = len(credit_inv)
        credit_ratio = n_credit / max(n_purchases, 1)

        unpaid_inv    = cust_inv[cust_inv["status"] == "unpaid"]
        n_unpaid      = len(unpaid_inv)
        unpaid_ratio  = n_unpaid / max(n_purchases, 1)
        total_unpaid  = float(unpaid_inv["total"].sum()) if not unpaid_inv.empty else 0.0

        # Balance utilization
        balance_ratio = bal / limit if limit > 0 else (1.0 if bal > 0 else 0.0)

        # Khata analysis
        cust_khata = khata_df[khata_df["party_id"] == cid] if not khata_df.empty else pd.DataFrame()
        n_debits   = 0
        n_credits  = 0
        payback    = 0.0

        if not cust_khata.empty:
            n_debits  = int((cust_khata["entry_type"] == "debit").sum())
            n_credits = int((cust_khata["entry_type"] == "credit").sum())
            total_deb = float(cust_khata[cust_khata["entry_type"]=="debit"]["amount"].sum())
            total_cre = float(cust_khata[cust_khata["entry_type"]=="credit"]["amount"].sum())
            payback   = total_cre / max(total_deb, 1)

        # Months active
        if n_purchases > 0:
            first = cust_inv["invoice_date"].min()
            last  = cust_inv["invoice_date"].max()
            months_active = max(1, (last - first).days // 30)
        else:
            months_active = 0

        # Credit label
        # Bad credit = high balance ratio OR high unpaid ratio OR low payback
        is_bad = int(
            balance_ratio > 0.8 or
            unpaid_ratio > 0.4 or
            (payback < 0.3 and n_debits > 3) or
            bal > 15000
        )

        feature_rows.append({
            "customer_id":         cid,
            "customer_name":       name,
            "balance":             bal,
            "credit_limit":        limit,
            "n_purchases":         n_purchases,
            "total_spend":         total_spend,
            "avg_purchase":        avg_purchase,
            "max_purchase":        max_purchase,
            "n_credit_purchases":  n_credit,
            "credit_ratio":        credit_ratio,
            "n_unpaid":            n_unpaid,
            "unpaid_ratio":        unpaid_ratio,
            "total_unpaid_amount": total_unpaid,
            "avg_days_to_pay":     999.0,
            "balance_ratio":       min(balance_ratio, 3.0),
            "n_khata_debits":      n_debits,
            "n_khata_credits":     n_credits,
            "khata_payback_ratio": min(payback, 2.0),
            "months_active":       months_active,
            "is_bad_credit":       is_bad,
        })

    df = pd.DataFrame(feature_rows)
    print(f"  Built features for {len(df)} customers")
    print(f"  Bad credit:  {df['is_bad_credit'].sum()} ({df['is_bad_credit'].mean()*100:.1f}%)")
    print(f"  Good credit: {(~df['is_bad_credit'].astype(bool)).sum()} ({(1-df['is_bad_credit'].mean())*100:.1f}%)")
    return df


FEATURE_COLS = [
    "n_purchases", "total_spend", "avg_purchase", "max_purchase",
    "credit_ratio", "unpaid_ratio", "total_unpaid_amount",
    "balance_ratio", "n_khata_debits", "n_khata_credits",
    "khata_payback_ratio", "months_active", "balance",
]


def train_logistic_baseline(X_train, X_test, y_train, y_test):
    """Logistic Regression — industry standard baseline"""
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
    """LightGBM — primary credit scoring model"""
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
    """
    Compute credit score 0-100 for each customer
    100 = excellent credit, 0 = very high risk
    """
    print("\n  Computing credit scores with SHAP...")
    X          = df[FEATURE_COLS]
    proba_bad  = lgbm_model.predict_proba(X)[:, 1]
    scores     = np.round((1 - proba_bad) * 100).astype(int)

    # SHAP explanations
    explainer  = shap.TreeExplainer(lgbm_model)
    shap_vals  = explainer.shap_values(X)
    if isinstance(shap_vals, list):
        shap_vals = shap_vals[1]

    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        score      = int(scores[i])
        prob_bad   = float(proba_bad[i])

        # Credit grade
        if score >= 80:
            grade     = "A"
            decision  = "Approve"
            max_credit = 20000
            color     = "green"
        elif score >= 65:
            grade     = "B"
            decision  = "Approve with caution"
            max_credit = 10000
            color     = "blue"
        elif score >= 50:
            grade     = "C"
            decision  = "Small credit only"
            max_credit = 5000
            color     = "yellow"
        elif score >= 35:
            grade     = "D"
            decision  = "Require advance payment"
            max_credit = 2000
            color     = "orange"
        else:
            grade     = "F"
            decision  = "Do not extend credit"
            max_credit = 0
            color     = "red"

        # SHAP top factors
        shap_row    = pd.Series(shap_vals[i], index=FEATURE_COLS)
        top_factors = shap_row.abs().nlargest(3).index.tolist()
        explanations = []
        for feat in top_factors:
            val  = float(row[feat])
            sv   = float(shap_row[feat])
            direction = "negative" if sv > 0 else "positive"
            if feat == "balance_ratio":
                explanations.append({
                    "factor":    "Credit utilization",
                    "value":     f"{val*100:.0f}%",
                    "impact":    direction,
                    "detail":    f"Using {val*100:.0f}% of credit limit"
                })
            elif feat == "unpaid_ratio":
                explanations.append({
                    "factor":    "Payment reliability",
                    "value":     f"{val*100:.0f}% unpaid",
                    "impact":    direction,
                    "detail":    f"{val*100:.0f}% of invoices unpaid"
                })
            elif feat == "khata_payback_ratio":
                explanations.append({
                    "factor":    "Udharo payback rate",
                    "value":     f"{val*100:.0f}%",
                    "impact":    direction,
                    "detail":    f"Pays back {val*100:.0f}% of credit given"
                })
            elif feat == "n_purchases":
                explanations.append({
                    "factor":    "Purchase history",
                    "value":     f"{int(val)} purchases",
                    "impact":    direction,
                    "detail":    f"{int(val)} total purchases on record"
                })
            elif feat == "balance":
                explanations.append({
                    "factor":    "Outstanding balance",
                    "value":     f"Rs {val:,.0f}",
                    "impact":    direction,
                    "detail":    f"Rs {val:,.0f} currently outstanding"
                })
            elif feat == "credit_ratio":
                explanations.append({
                    "factor":    "Credit usage frequency",
                    "value":     f"{val*100:.0f}%",
                    "impact":    direction,
                    "detail":    f"Buys on credit {val*100:.0f}% of the time"
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

    # Ensure we have both classes
    if y.nunique() < 2:
        print("  Only one class — adding synthetic bad credit cases")
        df.iloc[:5, df.columns.get_loc("is_bad_credit")] = 1
        y = df["is_bad_credit"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42,
        stratify=y if y.nunique() > 1 else None
    )

    # Train both models
    lr_model, lr_scaler, lr_metrics = train_logistic_baseline(
        X_train, X_test, y_train, y_test
    )
    lgbm_model, lgbm_metrics = train_lgbm_model(
        X_train, X_test, y_train, y_test
    )

    # Compare
    print(f"\n  Model Comparison:")
    print(f"  {'Metric':<12} {'LR (Baseline)':>15} {'LightGBM':>15}")
    print("  " + "-" * 44)
    for m in ["auc", "accuracy", "precision", "recall", "f1"]:
        print(f"  {m:<12} {lr_metrics.get(m, 0):>15.4f} {lgbm_metrics.get(m, 0):>15.4f}")

    # Credit scores
    scores = compute_credit_scores(lgbm_model, df)

    # Save
    print("\n  Saving models...")
    joblib.dump(lgbm_model, os.path.join(MODEL_DIR, f"credit_lgbm_{store_id}.pkl"))
    joblib.dump({"model": lr_model, "scaler": lr_scaler},
                os.path.join(MODEL_DIR, f"credit_lr_{store_id}.pkl"))

    meta = {
        "model":          "lgbm_with_lr_baseline",
        "version":        "1.0",
        "store_id":       store_id,
        "trained_on":     str(date.today()),
        "lgbm_metrics":   lgbm_metrics,
        "lr_metrics":     lr_metrics,
        "features":       FEATURE_COLS,
        "n_customers":    len(df),
        "n_bad_credit":   int(y.sum()),
    }
    with open(os.path.join(MODEL_DIR, f"credit_meta_{store_id}.json"), "w") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(MODEL_DIR, f"credit_scores_{store_id}.json"), "w") as f:
        json.dump({"scores": scores}, f, indent=2)

    print("  Models saved.")

    # Print results
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
