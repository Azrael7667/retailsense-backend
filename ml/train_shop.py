#!/usr/bin/env python
"""Trains the six AI helpers for ONE shop from an exported data folder, then publishes the results safely.

  python ml/train_shop.py --data DIR --out DIR --publish ml/results/<store_id> [--trials 30] [--only churn,credit] [--dry-run]

- The steps live in ml/train_cells.py. Each helper runs on its own: one failing or lacking data never stops the others.
- Results are published file by file (written next to the target, then swapped in). A helper that failed or was skipped
  keeps its previous results; nothing good is ever replaced by nothing.
- A shop with too little history is skipped with a clear reason.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


class NotEnoughData(Exception):
    """The shop has too little history for a step. The step is skipped; this is not an error."""


def read_blocks(path):
    blocks, cur = [], None
    for i, line in enumerate(open(path).read().split("\n"), start=1):
        m = re.match(r"# === BLOCK (\w+) (core|helper)\s*$", line)
        if m:
            if cur:
                blocks.append(cur)
            cur = {"name": m.group(1), "kind": m.group(2), "start": i + 1, "lines": []}
        elif cur is not None:
            cur["lines"].append(line)
    if cur:
        blocks.append(cur)
    return blocks


def snapshot(out):
    return {f: os.path.getmtime(os.path.join(out, f)) for f in os.listdir(out)}


def run_blocks(blocks, out, only, cells_name):
    ns = {"NotEnoughData": NotEnoughData, "__name__": "train_cells"}
    results, stop = [], False
    for b in blocks:
        if b["kind"] == "helper" and only and b["name"] not in only:
            continue
        if stop:
            results.append({"name": b["name"], "kind": b["kind"], "status": "not run", "detail": "an earlier core step did not finish", "seconds": 0, "files": []})
            continue
        code = "\n" * (b["start"] - 1) + "\n".join(b["lines"])     # keeps line numbers equal to the file
        before, t0 = snapshot(out), time.time()
        try:
            exec(compile(code, cells_name, "exec"), ns)
            status, detail = "ok", ""
        except NotEnoughData as e:
            status, detail = "skipped", str(e)
        except Exception as e:
            frames = [f for f in traceback.extract_tb(sys.exc_info()[2]) if f.filename == cells_name]
            where = f" ({cells_name} line {frames[-1].lineno})" if frames else ""
            status, detail = "failed", f"{type(e).__name__}: {e}{where}"
        after = snapshot(out)
        files = sorted(f for f, mt in after.items() if before.get(f) != mt)
        secs = round(time.time() - t0, 1)
        print(f"[{status.upper():7}] {b['name']:<12} {secs:>6}s  {detail}", flush=True)
        results.append({"name": b["name"], "kind": b["kind"], "status": status, "detail": detail, "seconds": secs,
                        "files": files if status == "ok" else []})
        if b["kind"] == "core" and status != "ok":
            stop = True
    return results, ns


def valid_results(path):
    if not path.endswith("_results.json"):
        return True
    try:
        with open(path) as f:
            return isinstance(json.load(f), dict)
    except Exception:
        return False


def publish(results, out, dest):
    """Copy the files of every helper that finished. Each file is written beside its target and swapped in."""
    os.makedirs(dest, exist_ok=True)
    published = []
    for r in results:
        if r["kind"] != "helper" or r["status"] != "ok":
            continue
        if not all(valid_results(os.path.join(out, f)) for f in r["files"]):
            r["status"], r["detail"] = "failed", "the results file could not be read back, so it was not published"
            continue
        for f in r["files"]:
            tmp = os.path.join(dest, f + ".tmp")
            shutil.copyfile(os.path.join(out, f), tmp)
            os.replace(tmp, os.path.join(dest, f))
            published.append(f)
    return published


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="folder with the exported CSV files of one shop")
    ap.add_argument("--out", required=True, help="scratch folder for this run")
    ap.add_argument("--publish", help="final results folder, normally ml/results/<store_id>")
    ap.add_argument("--trials", type=int, default=30, help="Optuna trials for the Business Direction helper")
    ap.add_argument("--only", help="comma separated helpers to run, e.g. churn,credit")
    ap.add_argument("--cells", default=os.path.join(HERE, "train_cells.py"))
    ap.add_argument("--dry-run", action="store_true", help="train but do not publish")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    os.environ.update({"DATA_DIR": os.path.abspath(a.data), "OUT_DIR": os.path.abspath(a.out), "N_TRIALS": str(a.trials)})
    only = {x.strip() for x in a.only.split(",")} if a.only else None
    print(f"training from {a.data}", flush=True)

    results, ns = run_blocks(read_blocks(a.cells), a.out, only, os.path.basename(a.cells))
    published = []
    if a.publish and not a.dry_run:
        published = publish(results, a.out, a.publish)
        status = {"run_at": datetime.now().isoformat(timespec="seconds"),
                  "data_through": str(ns["d1"].date()) if "d1" in ns else None,
                  "helpers": {r["name"]: {"status": r["status"], "detail": r["detail"], "seconds": r["seconds"]} for r in results if r["kind"] == "helper"},
                  "published": published}
        tmp = os.path.join(a.publish, "retrain_status.json.tmp")
        with open(tmp, "w") as f:
            json.dump(status, f, indent=1)
        os.replace(tmp, os.path.join(a.publish, "retrain_status.json"))

    ok = [r["name"] for r in results if r["kind"] == "helper" and r["status"] == "ok"]
    bad = [r["name"] for r in results if r["status"] == "failed"]
    skipped = [r["name"] for r in results if r["status"] in ("skipped", "not run")]
    print("\nsummary: finished", len(ok), "| skipped", len(skipped), "| failed", len(bad))
    print("published:", len(published), "files" if published else "(nothing)" if not a.dry_run else "(dry run, nothing published)")
    if bad:
        print("FAILED:", ", ".join(bad), "- their previous results were left in place")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
