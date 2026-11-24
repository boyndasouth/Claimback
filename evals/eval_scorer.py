"""Eval harness for Agent 12 (Success Scorer) — Day 5.

Loads the held-out 50 cases from ``data/generated/cases.jsonl`` (split==held_out),
runs the LightGBM scorer, and reports AUC.

Target: AUC >= 0.75 (SPEC §10).

Usage::

    python evals/eval_scorer.py                          # uses generated data
    python evals/eval_scorer.py --data path/to/cases.jsonl
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

_RESULTS_DIR = _REPO_ROOT / "evals" / "results"
_DEFAULT_DATA = _REPO_ROOT / "data" / "generated" / "cases.jsonl"

TARGET_AUC = 0.75


def _featurise(record: dict) -> list[float]:
    """Must match evals/train_scorer.py _featurise() exactly."""
    _CATEGORIES = [
        "medical_necessity", "prior_authorization", "coding_error",
        "out_of_network", "not_covered_benefit", "administrative",
    ]
    _PLAN_TYPES = ["employer", "marketplace", "medicaid", "medicare_advantage"]
    _STATES = [
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    ]
    vec: list[float] = []
    cat = record.get("gt_category", "administrative")
    for c in _CATEGORIES:
        vec.append(1.0 if cat == c else 0.0)
    pt = record.get("plan_type", "employer")
    for p in _PLAN_TYPES:
        vec.append(1.0 if pt == p else 0.0)
    st = str(record.get("state", "CA")).upper()
    for s in _STATES:
        vec.append(1.0 if st == s else 0.0)
    vec.append(float(record.get("billed_amount", 0) or 0))
    vec.append(0.0)
    vec.append(1.0 if record.get("gt_expedited_eligible") else 0.0)
    return vec


def run_eval(data_path: pathlib.Path) -> dict:
    import numpy as np

    # Load held-out split
    records = []
    with open(data_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                if r.get("split") == "held_out":
                    records.append(r)

    if not records:
        print("WARNING: no held_out records found in data file.")
        return {"error": "no held_out records"}

    print(f"Loaded {len(records)} held-out records.")

    # Try to load model
    model_path = _REPO_ROOT / "backend" / "models" / "success_scorer.pkl"
    if not model_path.exists():
        print(f"ERROR: model not found at {model_path}")
        print("Run 'python evals/train_scorer.py' first.")
        return {"error": f"model not found: {model_path}"}

    import joblib
    from sklearn.metrics import roc_auc_score

    booster = joblib.load(model_path)
    X = np.array([_featurise(r) for r in records], dtype=np.float32)
    y_true = np.array([int(r.get("gt_synthetic_outcome", 0)) for r in records])

    y_prob = booster.predict(X)
    auc = float(roc_auc_score(y_true, y_prob))

    print(f"Held-out AUC: {auc:.4f} (target >= {TARGET_AUC})")

    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_cases": len(records),
        "metrics": {"scorer_auc": round(auc, 4)},
        "targets": {"scorer_auc": TARGET_AUC},
        "passed": {"scorer_auc": auc >= TARGET_AUC},
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Eval: Success Scorer AUC")
    p.add_argument("--data", default=str(_DEFAULT_DATA))
    p.add_argument("--output-dir", default=str(_RESULTS_DIR))
    args = p.parse_args()

    data_path = pathlib.Path(args.data)
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}")
        print("Run 'make data' first.")
        return 1

    summary = run_eval(data_path)
    if "error" in summary:
        return 1

    out_dir = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"eval_scorer_{datetime.date.today()}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Results written to {out}")

    auc = summary["metrics"]["scorer_auc"]
    if not summary["passed"]["scorer_auc"]:
        print(f"FAIL: AUC {auc:.4f} < {TARGET_AUC}")
        return 1
    print(f"PASS: AUC {auc:.4f} >= {TARGET_AUC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
