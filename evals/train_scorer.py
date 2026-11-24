"""LightGBM success scorer training script (§9, §12).

Loads the synthetic outcomes JSONL produced by ``make data``, trains a binary
LightGBM classifier to predict appeal success, and serialises the fitted model
to ``backend/models/success_scorer.pkl``.

**Transparency note (SPEC §9):** The ground-truth outcome labels are generated
by a deterministic rule-based simulator inside ``data/generate.py`` (function
``_build_case``), not by real claims data.  The simulator encodes domain
heuristics (coding errors -> higher success, out-of-network -> lower) with
additive probability adjustments and a Bernoulli draw.  The resulting model
therefore reflects those heuristics, not real-world payer behaviour.  This
limitation is stated here and in the README so evaluators understand what the
AUC metric measures.

Usage::

    python evals/train_scorer.py                        # uses data/generated/cases.jsonl
    python evals/train_scorer.py --data path/to/cases.jsonl --output-dir ./backend/models
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import joblib
import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

try:
    import lightgbm as lgb
except ImportError as exc:
    sys.exit(f"lightgbm is required: pip install lightgbm  ({exc})")

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DEFAULT_DATA = _REPO_ROOT / "data" / "generated" / "cases.jsonl"
_DEFAULT_OUTPUT = _REPO_ROOT / "backend" / "models"

# Feature columns (must match agents/success_scorer.py _CATEGORIES, _PLAN_TYPES, _STATES)
_CATEGORIES = [
    "medical_necessity",
    "prior_authorization",
    "coding_error",
    "out_of_network",
    "not_covered_benefit",
    "administrative",
]
_PLAN_TYPES = ["employer", "marketplace", "medicaid", "medicare_advantage"]
_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]


def _build_feature_names() -> list[str]:
    names: list[str] = []
    names += [f"cat_{c}" for c in _CATEGORIES]
    names += [f"plan_{p}" for p in _PLAN_TYPES]
    names += [f"state_{s}" for s in _STATES]
    names += ["billed_amount", "code_issues_count", "expedited_eligible"]
    return names


def _featurise(record: dict) -> list[float]:
    """Convert one JSONL record to a numeric feature vector."""
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
    vec.append(0.0)  # code_issues_count — not stored in JSONL; use 0 for training
    vec.append(1.0 if record.get("gt_expedited_eligible") else 0.0)
    return vec


def load_data(data_path: pathlib.Path) -> tuple[np.ndarray, np.ndarray]:
    """Load JSONL and return (X, y) arrays."""
    records = []
    with open(data_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    X = np.array([_featurise(r) for r in records], dtype=np.float32)
    y = np.array([int(r.get("gt_synthetic_outcome", 0)) for r in records], dtype=np.int32)
    return X, y


def train(data_path: pathlib.Path, output_dir: pathlib.Path) -> float:
    """Train LightGBM and return held-out AUC."""
    print(f"Loading data from {data_path} ...")
    X, y = load_data(data_path)
    print(f"  {len(X)} records loaded. Positive rate: {y.mean():.2%}")

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    feature_names = _build_feature_names()
    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    params = {
        "objective": "binary",
        "metric": "auc",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "random_state": 42,
        "verbose": -1,
    }

    print("Training LightGBM ...")
    booster = lgb.train(
        params,
        dtrain,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)],
    )

    val_preds = booster.predict(X_val)
    auc = roc_auc_score(y_val, val_preds)
    print(f"  Validation AUC: {auc:.4f} (target >= 0.75)")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "success_scorer.pkl"
    joblib.dump(booster, out_path)
    print(f"  Model saved to {out_path}")

    return auc


def main() -> int:
    p = argparse.ArgumentParser(description="Train the ClaimBack success scorer.")
    p.add_argument(
        "--data",
        default=str(_DEFAULT_DATA),
        help="Path to cases.jsonl (default: data/generated/cases.jsonl)",
    )
    p.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT),
        help="Directory to save success_scorer.pkl",
    )
    args = p.parse_args()

    data_path = pathlib.Path(args.data)
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}")
        print("Run 'make data' first to generate synthetic cases.")
        return 1

    auc = train(data_path, pathlib.Path(args.output_dir))
    if auc < 0.75:
        print(f"WARNING: AUC {auc:.4f} is below the 0.75 target.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
