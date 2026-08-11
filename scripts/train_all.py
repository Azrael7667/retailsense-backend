"""
RetailSense Nepal - Train All Models
Run: python scripts/train_all.py
"""

import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

STORE_ID = "58998cb1-3a7c-4961-abe5-09df4d28c8d9"

def run(name, fn):
    print(f"\n{'='*50}")
    print(f"Training: {name}")
    print('='*50)
    start = time.time()
    try:
        fn(STORE_ID)
        print(f"Done: {name} in {time.time()-start:.1f}s")
        return True
    except Exception as e:
        print(f"Failed: {name} — {e}")
        return False

if __name__ == "__main__":
    results = {}
    total_start = time.time()

    from ml.training.cash_flow_model        import train as train_cashflow
    from ml.training.inventory_demand_model import train as train_inventory
    from ml.training.churn_model            import train as train_churn
    from ml.training.sales_trend_model      import train as train_trend
    from ml.training.anomaly_model          import train as train_anomaly
    from ml.training.credit_score_model     import train as train_credit

    results["Cash Flow"]   = run("Cash Flow Forecast (Prophet)",          train_cashflow)
    results["Inventory"]   = run("Inventory Demand (LightGBM x97)",       train_inventory)
    results["Churn"]       = run("Customer Churn (LightGBM+SHAP)",        train_churn)
    results["Sales Trend"] = run("Sales Trend (Prophet+Optuna)",          train_trend)
    results["Anomaly"]     = run("Anomaly Detection (Isolation Forest)",  train_anomaly)
    results["Credit"]      = run("Credit Scoring (LightGBM+LR)",          train_credit)

    print(f"\n{'='*50}")
    print(f"All done — {time.time()-total_start:.0f}s total")
    print('='*50)
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
