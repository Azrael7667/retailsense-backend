#!/usr/bin/env bash
# Retrains the AI helpers for one or more shops and publishes the results.
#   ml/retrain.sh                 every shop listed in ml/shops.txt
#   ml/retrain.sh <store_id> ...  only these shops
# Optional settings in front of the command:
#   RESULTS_DIR=/some/folder  publish somewhere else (a trial run that leaves the live results alone)
#   TRIALS=10                 fewer Optuna trials for a quicker run (default 30)
# Weekly schedule (Sunday 02:00), add with `crontab -e`:
#   0 2 * * 0 /home/solomon/retailsense-backend/ml/retrain.sh
# Step 1 (backend Python)  exports the shop's data.   Step 2 (ML Python)  trains and publishes to ml/results/<store_id>/.
# Everything is written to ml/logs/retrain_<time>.log as well. Exit code 0 means no helper failed.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PY="${BACKEND_PY:-$ROOT/venv/bin/python}"
ML_PY="${ML_PY:-$HOME/retailsense-ml/venv/bin/python}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/ml/results}"
TRIALS="${TRIALS:-30}"
mkdir -p "$ROOT/ml/logs"
exec > >(tee -a "$ROOT/ml/logs/retrain_$(date +%Y%m%d_%H%M%S).log") 2>&1

[ -x "$ML_PY" ] || { echo "ML Python not found at $ML_PY (see ml/requirements-ml.txt)"; exit 1; }
SHOPS=("$@")
if [ ${#SHOPS[@]} -eq 0 ]; then
  mapfile -t SHOPS < <(grep -v '^[[:space:]]*#' "$ROOT/ml/shops.txt" | awk 'NF {print $1}')
fi
[ ${#SHOPS[@]} -gt 0 ] || { echo "no shops to train (add store ids to ml/shops.txt)"; exit 1; }

status=0
for SID in "${SHOPS[@]}"; do
  echo "=== $(date '+%F %T') shop $SID"
  WORK="$(mktemp -d)"
  if (cd "$ROOT" && "$BACKEND_PY" ml/export_shop.py --store-id "$SID" --out "$WORK/data"); then
    "$ML_PY" "$ROOT/ml/train_shop.py" --data "$WORK/data" --out "$WORK/out" --publish "$RESULTS_DIR/$SID" --trials "$TRIALS" || status=1
  else
    echo "export failed for $SID, nothing was trained or changed"
    status=1
  fi
  rm -rf "$WORK"          # the temporary export never stays on disk
done
echo "=== done $(date '+%F %T') exit $status"
exit $status
