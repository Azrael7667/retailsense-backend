"""Builds ml/train_cells.py from the Kaggle notebook, so the script runs exactly the code that produced your results.

  python ml/build_train_cells.py ml/notebooks/retailsense_models.ipynb

Only the data loading (cell 2) and the report (cells 7 and 13) are replaced; every training cell is taken from the notebook as it is.
Cells are recognised by their header line, for example  # ===== CELL 5: Business Direction ... =====
"""
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

TOP = r'''import glob, os, sys, json, hashlib, logging, warnings, platform
from datetime import date
import numpy as np
import pandas as pd
import optuna
from prophet import Prophet
from prophet.serialize import model_to_json

warnings.filterwarnings("ignore")
logging.getLogger("cmdstanpy").setLevel(logging.ERROR)
logging.getLogger("prophet").setLevel(logging.ERROR)
optuna.logging.set_verbosity(optuna.logging.WARNING)

DATA = os.environ["DATA_DIR"]
OUT = os.environ["OUT_DIR"]
os.makedirs(OUT, exist_ok=True)
TRAINED_ON = date.today().isoformat()
np.random.seed(42)
INCLUDE_PURCHASES = False   # True: cash outflow also counts stock purchases (net cash flow then looks much lower)
import prophet as prophet_pkg
import cmdstanpy
cmdstanpy.disable_logging()
print("data:", DATA, "| numpy", np.__version__, "| pandas", pd.__version__, "| prophet", prophet_pkg.__version__, "| optuna", optuna.__version__)


# the least history each helper needs; a shop with less is skipped with a clear message instead of getting junk results
MIN_WEEKS_FORECAST = 40
MIN_WEEKS_RESTOCK = 36
MIN_DAYS_CHURN = 270
MIN_DAYS_CREDIT = 330
MIN_CUSTOMERS = 30
MIN_INVOICES = 200

def need(ok, why):
    if not ok:
        raise NotEnoughData(why)
'''

DUMP_CLEAN = r'''
def _clean(o):                                    # JSON cannot hold NaN or numpy types
    if isinstance(o, dict): return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.bool_,)): return bool(o)
    if isinstance(o, (np.floating, float)): return None if (np.isnan(o) or np.isinf(o)) else float(o)
    if isinstance(o, pd.Timestamp): return str(o.date())
    return o

def dump(obj, name):
    with open(f"{OUT}/{name}.json", "w") as f:
        json.dump(_clean(obj), f, indent=1, default=str)
'''

REPORT = r'''import hashlib, platform
import sklearn
sha = hashlib.sha256(open(f"{DATA}/invoices.csv", "rb").read()).hexdigest()[:16]
dump({"trained_on": TRAINED_ON, "python": platform.python_version(),
      "versions": {"numpy": np.__version__, "pandas": pd.__version__, "optuna": optuna.__version__, "prophet": prophet_pkg.__version__,
                   "lightgbm": lgb.__version__, "shap": shap.__version__, "scikit-learn": sklearn.__version__},
      "data": {"from": str(d0.date()), "to": str(d1.date()), "invoices": len(inv), "invoices_sha256_16": sha, "weeks_used": len(rev_w),
               "customers_with_invoices": int(inv_c["customer_id"].nunique()), "products_active": len(act) if "act" in globals() else None}}, "model_report")
'''


def guard(msg, ok_expr):
    return f"need({ok_expr}, {msg})\n"


def add_after(text, pattern, extra, label):
    m = re.search(pattern, text, re.M)
    if not m:
        print(f"note: could not place the check '{label}'; that step runs without it")
        return text
    return text[:m.end()] + extra + text[m.end():]


HEADER = re.compile(r"\A\s*#[\s=\-_*]*cell\s+(\w+)\b", re.I)      # "# ===== CELL 5: ...", "# CELL 5 - ...", "# --- Cell 6b ---"
# if a cell has no header, it is recognised by a line only that cell contains (checked in this order)
SIGNATURES = [("6b", "model_comparison_weekly_revenue"), ("5", "sales_trend_model"), ("6", "cash_flow_revenue_model"),
              ("9", "churn_lightgbm"), ("10", "credit_logistic_regression"), ("11", "restock_lightgbm"),
              ("12", "anomaly_isolation_forest"), ("8", "import lightgbm as lgb"), ("4", "def holdout_eval"), ("3", "rev_w =")]


def cell_id(src):
    m = HEADER.match(src)
    if m:
        return m.group(1).lower(), True
    for cid, token in SIGNATURES:
        if token in src:
            return cid, False
    return None, False


def read_cells(path):
    nb = json.load(open(path, encoding="utf-8"))
    found, listing = {}, []
    for i, c in enumerate(nb.get("cells", [])):
        if c.get("cell_type") != "code":
            continue
        src = c["source"] if isinstance(c["source"], str) else "".join(c["source"])
        src = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith(("!", "%")))     # notebook-only commands
        cid, had_header = cell_id(src)
        listing.append((i, cid, next((l for l in src.split("\n") if l.strip()), "")[:70]))
        if cid is None:
            continue
        if cid in found:
            print(f"note: two cells look like cell {cid}; using the first one")
            continue
        body = re.sub(r"\A\s*#[^\n]*\n", "", src, count=1) if had_header else src
        found[cid] = body.strip("\n") + "\n"
    return found, listing


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cells, listing = read_cells(sys.argv[1])
    missing = [c for c in ("3", "4", "5", "6", "8", "9", "10", "11", "12") if c not in cells]
    if missing:
        print(f"these cells were not found in the notebook: {missing}\nwhat the notebook contains (code cell number | recognised as | first line):")
        for i, cid, first in listing:
            print(f"  {i:>3} | {cid or '-':<4} | {first}")
        if not listing:
            print("  (no code cells at all: is this the right notebook file?)")
        sys.exit(1)

    weeks = 'f"only {len(rev_w)} complete weeks of sales, need {MIN_WEEKS_FORECAST}"'
    cell3 = add_after(cells["3"], r'^inv = pd\.read_csv\(f"\{DATA\}/invoices\.csv".*\n',
                      guard('f"only {len(inv)} invoices in the export"', "len(inv) >= 50"), "enough invoices")
    cell10 = add_after(cells["10"], r'^print\(f"labelled customers:.*\n',
                       guard('f"only {len(yc)} customers with a known outcome, or too few on one side, need {MIN_CUSTOMERS} and at least 5 of each"',
                             "len(yc) >= MIN_CUSTOMERS and yc.nunique() == 2 and int(yc.value_counts().min()) >= 5"), "enough labelled customers")
    cell11 = add_after(cells["11"], r'^W = D\.shape\[1\].*\n',
                       guard('f"only {W} complete weeks and {len(act)} active products, need {MIN_WEEKS_RESTOCK} weeks"',
                             "W >= MIN_WEEKS_RESTOCK and len(act) >= 20"), "enough weeks for restock")

    blocks = [
        ("setup", "core", TOP + "\n" + cell3 + "\n" + cells["4"] + DUMP_CLEAN),
        ("sales_trend", "helper", guard(weeks, "len(rev_w) >= MIN_WEEKS_FORECAST") + cells["5"]),
        ("cash_flow", "helper", guard(weeks, "len(rev_w) >= MIN_WEEKS_FORECAST") + cells["6"]),
    ]
    if "6b" in cells:
        blocks.append(("comparison", "helper", guard(weeks, "len(rev_w) >= MIN_WEEKS_FORECAST")
                       + guard('"needs the sales_trend and cash_flow steps to have run"', '"best" in globals() and "DEFAULT" in globals()') + cells["6b"]))
    blocks += [
        ("part2_setup", "core", cells["8"]),
        ("churn", "helper", guard('f"only {inv_c[\'customer_id\'].nunique()} customers have bought anything, need {MIN_CUSTOMERS}"',
                                  "inv_c['customer_id'].nunique() >= MIN_CUSTOMERS")
         + guard('f"only {(d1 - d0).days} days of history, need {MIN_DAYS_CHURN}"', "(d1 - d0).days >= MIN_DAYS_CHURN") + cells["9"]),
        ("credit", "helper", guard('f"only {(d1 - d0).days} days of history, need {MIN_DAYS_CREDIT}"', "(d1 - d0).days >= MIN_DAYS_CREDIT") + cell10),
        ("restock", "helper", cell11),
        ("anomaly", "helper", guard('f"only {len(inv)} invoices, need {MIN_INVOICES}"', "len(inv) >= MIN_INVOICES") + cells["12"]),
        ("report", "helper", REPORT),
    ]
    out = ("# Generated by ml/build_train_cells.py from the notebook. Do not edit by hand; edit the notebook and build again.\n"
           "# ml/train_shop.py runs these blocks one after another in a shared namespace.\n"
           "# Marker format:   # === BLOCK <name> <core|helper>\n")
    for name, kind, text in blocks:
        out += f"\n# === BLOCK {name} {kind}\n" + text.rstrip("\n") + "\n"

    leftovers = [l.strip() for l in out.split("\n") if "/kaggle" in l or "make_archive" in l]
    if leftovers:
        sys.exit("Kaggle-only lines are still in the notebook cells, remove them in the notebook first:\n  " + "\n  ".join(leftovers))
    ast.parse(out)
    with open(os.path.join(HERE, "train_cells.py"), "w") as f:
        f.write(out)
    print("wrote ml/train_cells.py with steps:", ", ".join(b[0] for b in blocks), f"({out.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
