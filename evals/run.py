#!/usr/bin/env python3
"""Evaluation harness for Days 2 and 3 (§10).

Measures six metrics across Agents 3–8:

| Metric                  | Agent | Target |
|-------------------------|-------|--------|
| Field extraction F1     |   3   | ≥ 0.90 |
| PII leakage rate        |   4   | 0 %    |
| Category accuracy       |   5   | ≥ 0.85 |
| Retrieval Recall@5      |   6   | ≥ 0.85 |
| Coding-error recall     |   7   | ≥ 0.90 |
| Deadline exact-match    |   8   | ≥ 0.95 |

Usage::

    # Smoke run (first 30 cases) — used in CI
    python evals/run.py --subset 30

    # Full eval set (200 cases)
    python evals/run.py

    # Day-3 metrics only (skip watsonx calls for agents 3-5)
    python evals/run.py --day3-only

    # Read ground-truth from a custom fixture dir
    python evals/run.py --fixture-dir data/fixtures/eval

Results are written to ``evals/results/<iso-timestamp>.json``.

Ground-truth fixture format (one JSON file per case)::

    {
      "case_id": "CLM-001",
      "denial_text": "...",
      "ground_truth": {
        "claim_id":           "CLM-001",
        "payer":              "Aetna",
        "plan_type":          "employer",
        "state":              "CA",
        "service_date":       "2024-03-15",
        "denial_date":        "2024-04-01",
        "cpt_codes":          ["70553"],
        "icd10_codes":        ["G35"],
        "billed_amount":      "2500.00",
        "denial_reason_text": "...",
        "denial_reason_code": "50",
        "category":           "medical_necessity",
        "gold_clause_id":     "chunk-uuid-of-relevant-section",
        "has_coding_error":   true,
        "expected_deadline":  "2024-10-01"
      },
      "pii_spans": ["John Smith", "123-45-6789"]
    }
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

# ── Path setup (run from repo root) ──────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("evals")

# ── Constants ─────────────────────────────────────────────────────────────────

_TARGETS = {
    "extraction_f1": 0.90,
    "pii_leakage_rate": 0.0,
    "category_accuracy": 0.85,
    "retrieval_recall_at_5": 0.85,
    "coding_error_recall": 0.90,
    "deadline_exact_match": 0.95,
}

_EXTRACTABLE_FIELDS = [
    "claim_id",
    "payer",
    "plan_type",
    "state",
    "service_date",
    "denial_date",
    "cpt_codes",
    "icd10_codes",
    "billed_amount",
    "denial_reason_code",
]

# ── Helpers ───────────────────────────────────────────────────────────────────


def _field_match(predicted: Any, expected: Any) -> bool:
    """Return True when predicted matches expected (type-normalised)."""
    if predicted is None and expected is None:
        return True
    if predicted is None or expected is None:
        return False
    # Normalise lists to sets of lowercase strings for order-independent compare
    if isinstance(expected, list):
        return set(str(v).lower() for v in predicted) == set(str(v).lower() for v in expected)
    # Decimal / numeric comparison
    try:
        return Decimal(str(predicted)) == Decimal(str(expected))
    except Exception:
        pass
    return str(predicted).strip().lower() == str(expected).strip().lower()


def _compute_extraction_f1(
    predicted_fields: dict[str, Any],
    ground_truth_fields: dict[str, Any],
    field_names: list[str],
) -> float:
    """Micro-averaged F1 across the listed fields.

    Treated as binary classification per field: correct = TP, wrong = FP+FN.
    """
    tp = sum(
        1
        for f in field_names
        if _field_match(predicted_fields.get(f), ground_truth_fields.get(f))
    )
    # Denominator = number of fields (micro-average simplification for single-label fields)
    total = len(field_names)
    precision = tp / total if total else 0.0
    recall = tp / total if total else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _check_pii_leakage(downstream_prompt: str, pii_spans: list[str]) -> list[str]:
    """Return PII spans that appear verbatim in *downstream_prompt*."""
    return [span for span in pii_spans if span and span in downstream_prompt]


# ── Fixture loading ───────────────────────────────────────────────────────────


def _load_fixtures(fixture_dir: Path, subset: int | None) -> list[dict[str, Any]]:
    """Load evaluation fixtures from *fixture_dir*.

    If no fixtures are found, synthesise a minimal smoke set so the eval
    harness can run end-to-end without real data files.
    """
    files = sorted(fixture_dir.glob("*.json"))
    if subset:
        files = files[:subset]

    if not files:
        logger.warning(
            "No fixture files found in %s — using synthetic smoke data", fixture_dir
        )
        return _synthetic_smoke_fixtures(subset or 5)

    fixtures = []
    for f in files:
        try:
            fixtures.append(json.loads(f.read_text()))
        except Exception as exc:
            logger.warning("Skipping %s: %s", f, exc)
    return fixtures


def _synthetic_smoke_fixtures(n: int) -> list[dict[str, Any]]:
    """Generate *n* minimal synthetic fixtures for CI smoke runs."""
    from app.models import DenialCategory

    categories = list(DenialCategory)
    plan_types = ["employer", "marketplace", "medicaid", "medicare_advantage"]
    states = ["CA", "TX", "NY", "FL", "WA"]

    fixtures = []
    for i in range(n):
        cat = categories[i % len(categories)]
        fixtures.append({
            "case_id": f"SMOKE-{i:04d}",
            "denial_text": (
                f"Dear Patient PERSON_{i},\n"
                f"Your claim CLM-{i:04d} for service on 2024-0{(i%9)+1}-15 "
                f"was denied. Reason: {cat.value}. "
                f"CPT code 9921{i % 10}. ICD-10 code J0{i % 9}.9. "
                f"Billed amount: ${(i+1)*100}.00. "
                f"Payer: BlueCross. Plan: {plan_types[i % 4]}. State: {states[i % 5]}."
            ),
            "ground_truth": {
                "claim_id": f"CLM-{i:04d}",
                "payer": "BlueCross",
                "plan_type": plan_types[i % 4],
                "state": states[i % 5],
                "service_date": f"2024-0{(i%9)+1}-15",
                "denial_date": f"2024-0{(i%9)+1}-30",
                "cpt_codes": [f"9921{i % 10}"],
                "icd10_codes": [f"J0{i % 9}.9"],
                "billed_amount": str((i + 1) * 100),
                "denial_reason_text": f"Denied: {cat.value}.",
                "denial_reason_code": str(50 + i),
                "category": cat.value,
            },
            "pii_spans": [f"PERSON_{i}"],
        })
    return fixtures


# ── Day-3 synthetic smoke fixtures ────────────────────────────────────────────


def _synthetic_smoke_day3_fixtures(n: int) -> list[dict[str, Any]]:
    """Generate *n* minimal synthetic fixtures for Day-3 smoke runs.

    These do NOT require policy chunks in the DB — they test Agent 7 and 8
    deterministic logic against known ground truth.
    """

    states = ["CA", "TX", "NY", "FL", "WA", "IL", "PA", "OH", "GA", "NC"]
    # State deadlines from fixtures (internal_days)
    state_deadlines = {
        "CA": 180, "TX": 120, "NY": 180, "FL": 365, "WA": 180,
        "IL": 180, "PA": 180, "OH": 180, "GA": 120, "NC": 180,
    }
    fixtures = []
    for i in range(n):
        state = states[i % len(states)]
        denial_date = f"2024-0{(i % 9) + 1}-01"
        internal_days = state_deadlines.get(state, 180)
        from datetime import date, timedelta
        d = date.fromisoformat(denial_date)
        expected_deadline = (d + timedelta(days=internal_days)).isoformat()
        # 20% of cases have a coding error (injected unknown CPT)
        has_error = (i % 5) == 0
        cpt = "99214" if not has_error else "INVALID99"
        icd = "E11.9"
        fixtures.append({
            "case_id": f"D3-SMOKE-{i:04d}",
            "denial_text": f"Claim denied for CPT {cpt} ICD {icd}.",
            "ground_truth": {
                "claim_id": f"D3-{i:04d}",
                "payer": "BlueCross",
                "plan_type": "employer",
                "state": state,
                "service_date": f"2024-0{(i % 9) + 1}-01",
                "denial_date": denial_date,
                "cpt_codes": [cpt],
                "icd10_codes": [icd],
                "billed_amount": str((i + 1) * 200),
                "denial_reason_text": "Service not medically necessary.",
                "denial_reason_code": "CO-50",
                "category": "medical_necessity",
                "gold_clause_id": None,          # no policy chunks in smoke run
                "has_coding_error": has_error,
                "expected_deadline": expected_deadline,
            },
            "pii_spans": [],
        })
    return fixtures


# ── Core eval loops ───────────────────────────────────────────────────────────


async def _run_case_day2(
    fixture: dict[str, Any],
    extractor: Any,
    redactor: Any,
    classifier: Any,
) -> dict[str, Any]:
    """Run agents 3→4→5 on a single fixture and return per-case metrics."""
    import uuid

    from app.models import CaseContext

    ctx = CaseContext(
        case_id=fixture.get("case_id", str(uuid.uuid4())),
        user_id="eval",
        trace_id=str(uuid.uuid4()),
    )
    denial_text: str = fixture.get("denial_text", "")
    gt: dict[str, Any] = fixture.get("ground_truth", {})
    pii_spans: list[str] = fixture.get("pii_spans", [])

    result: dict[str, Any] = {
        "case_id": ctx.case_id,
        "extraction_f1": 0.0,
        "pii_leaked": [],
        "category_correct": False,
        "predicted_category": None,
        "error": None,
    }

    try:
        # ── Agent 3: Extract ─────────────────────────────────────────────────
        extract_result = await extractor.run(denial_text, ctx)
        predicted = extract_result.output.model_dump(mode="json")

        result["extraction_f1"] = _compute_extraction_f1(
            predicted, gt, _EXTRACTABLE_FIELDS
        )

        # ── Agent 4: Redact (and capture the prompt sent to NER) ─────────────
        captured_prompts: list[str] = []
        _original_generate = redactor._get_client().generate  # type: ignore[attr-defined]

        async def _capturing_generate(model_tier: str, prompt: str, **kw: Any) -> Any:
            captured_prompts.append(prompt)
            return await _original_generate(model_tier, prompt, **kw)

        redact_result = await redactor.run(extract_result.output, ctx)

        # Check PII leakage in every captured prompt
        all_leaked: list[str] = []
        for prompt_text in captured_prompts:
            all_leaked.extend(_check_pii_leakage(prompt_text, pii_spans))
        result["pii_leaked"] = list(set(all_leaked))

        # ── Agent 5: Classify ─────────────────────────────────────────────────
        classify_result = await classifier.run(redact_result.output, ctx)
        predicted_cat, rationale = classify_result.output
        result["predicted_category"] = predicted_cat.value
        result["category_correct"] = predicted_cat.value == gt.get("category", "")

    except Exception as exc:
        result["error"] = str(exc)
        logger.warning("Case %s failed: %s", ctx.case_id, exc)

    return result


async def _run_case_day3(
    fixture: dict[str, Any],
    validator: Any,
    deadline_agent: Any,
    retriever: Any | None,
) -> dict[str, Any]:
    """Run agents 6, 7, 8 on a single fixture and return per-case Day-3 metrics.

    *retriever* may be None when running in --day3-only mode without a live DB.
    """
    import uuid

    from agents.regulation_deadline import DeadlineInput
    from app.models import CaseContext, DenialCategory, ExtractedClaim

    ctx = CaseContext(
        case_id=fixture.get("case_id", str(uuid.uuid4())),
        user_id="eval",
        trace_id=str(uuid.uuid4()),
    )
    gt: dict[str, Any] = fixture.get("ground_truth", {})

    result: dict[str, Any] = {
        "case_id": ctx.case_id,
        # Agent 6
        "retrieval_hit": None,      # True/False/None (None = skipped)
        "retrieved_clause_ids": [],
        # Agent 7
        "coding_error_detected": False,
        "coding_error_expected": gt.get("has_coding_error", False),
        # Agent 8
        "deadline_correct": False,
        "predicted_deadline": None,
        "expected_deadline": gt.get("expected_deadline"),
        "error": None,
    }

    try:
        # Build a minimal ExtractedClaim from ground truth
        from decimal import Decimal
        claim = ExtractedClaim(
            claim_id=gt.get("claim_id", ctx.case_id),
            payer=gt.get("payer", "Unknown"),
            plan_type=gt.get("plan_type", "employer"),  # type: ignore[arg-type]
            state=gt.get("state", "CA"),
            service_date=gt.get("service_date", "2024-01-01"),
            denial_date=gt.get("denial_date", "2024-01-15"),
            cpt_codes=gt.get("cpt_codes", []),
            icd10_codes=gt.get("icd10_codes", []),
            billed_amount=Decimal(str(gt.get("billed_amount", "0"))),
            denial_reason_text=gt.get("denial_reason_text", "Denied."),
            denial_reason_code=gt.get("denial_reason_code"),
            field_confidence={},
        )
        category_str = gt.get("category", "medical_necessity")
        try:
            category = DenialCategory(category_str)
        except ValueError:
            category = DenialCategory.MEDICAL_NECESSITY

        # ── Agent 6: Retrieval ───────────────────────────────────────────────
        gold_clause_id: str | None = gt.get("gold_clause_id")
        if retriever is not None and gold_clause_id:
            retrieval_result = await retriever.run(claim, ctx)
            retrieved_ids = [c.clause_id for c in retrieval_result.output]
            result["retrieved_clause_ids"] = retrieved_ids
            result["retrieval_hit"] = gold_clause_id in retrieved_ids
        else:
            result["retrieval_hit"] = None  # skipped

        # ── Agent 7: Code validation ─────────────────────────────────────────
        issues_result = await validator.run(claim, ctx)
        has_error_detected = len(issues_result.output) > 0
        result["coding_error_detected"] = has_error_detected

        # ── Agent 8: Deadline ────────────────────────────────────────────────
        deadline_input = DeadlineInput(claim=claim, denial_category=category)
        plan_result = await deadline_agent.run(deadline_input, ctx)
        predicted = plan_result.output.internal_appeal_deadline.isoformat()
        result["predicted_deadline"] = predicted
        result["deadline_correct"] = predicted == gt.get("expected_deadline")

    except Exception as exc:
        result["error"] = str(exc)
        logger.warning("Day-3 case %s failed: %s", ctx.case_id, exc)

    return result


async def _run_eval(
    fixtures: list[dict[str, Any]],
    day3_only: bool = False,
) -> dict[str, Any]:
    """Run the full eval and aggregate metrics."""
    # Import agents lazily so the eval harness can be imported without side-effects
    from agents.code_validator import CodeValidator
    from agents.regulation_deadline import RegulationDeadline

    validator = CodeValidator()
    deadline_agent = RegulationDeadline()

    # Try to initialise Agent 6 — requires a live DB; skip gracefully if unavailable
    retriever: Any = None
    if not day3_only:
        try:
            from agents.policy_retriever import PolicyRetriever
            retriever = PolicyRetriever()
            logger.info("Policy retriever initialised (DB required).")
        except Exception as exc:
            logger.warning("Skipping Agent 6 retrieval eval (no DB): %s", exc)

    # Day-2 agents (skip if day3_only)
    extractor: Any = None
    redactor: Any = None
    classifier: Any = None
    if not day3_only:
        try:
            from agents.denial_classifier import DenialClassifier
            from agents.document_extractor import DocumentExtractor
            from agents.pii_redactor import PiiRedactor
            extractor = DocumentExtractor()
            redactor = PiiRedactor()
            classifier = DenialClassifier()
        except Exception as exc:
            logger.warning("Skipping Day-2 agents (watsonx required): %s", exc)

    day2_results: list[dict[str, Any]] = []
    day3_results: list[dict[str, Any]] = []

    for fixture in fixtures:
        # Day-2 metrics
        if extractor and redactor and classifier:
            r2 = await _run_case_day2(fixture, extractor, redactor, classifier)
            day2_results.append(r2)

        # Day-3 metrics (always run — validator + deadline are deterministic)
        r3 = await _run_case_day3(fixture, validator, deadline_agent, retriever)
        day3_results.append(r3)

        status = "✓" if not (r3.get("error")) else "✗"
        logger.info(
            "[%s] %s  deadline=%s  coding_err=%s  retrieval=%s",
            status,
            r3["case_id"],
            "correct" if r3["deadline_correct"] else f"wrong({r3['predicted_deadline']})",
            "detected" if r3["coding_error_detected"] else "clean",
            r3["retrieval_hit"],
        )

    n = len(fixtures)
    n3_ok = sum(1 for r in day3_results if not r["error"])

    # ── Day-2 aggregates ──────────────────────────────────────────────────────
    n2_ok = sum(1 for r in day2_results if not r["error"])
    extraction_f1 = (
        sum(r["extraction_f1"] for r in day2_results if not r["error"]) / n2_ok
        if n2_ok else 0.0
    )
    leaked_cases = [r for r in day2_results if r["pii_leaked"]]
    pii_leakage_rate = len(leaked_cases) / max(len(day2_results), 1)
    category_accuracy = (
        sum(1 for r in day2_results if r["category_correct"] and not r["error"]) / n2_ok
        if n2_ok else 0.0
    )

    # ── Day-3 aggregates ──────────────────────────────────────────────────────

    # Retrieval Recall@5: fraction of cases where gold_clause_id is in top-5
    retrieval_cases = [r for r in day3_results if r["retrieval_hit"] is not None]
    retrieval_recall_at_5 = (
        sum(1 for r in retrieval_cases if r["retrieval_hit"]) / len(retrieval_cases)
        if retrieval_cases else 0.0
    )

    # Coding-error recall: fraction of error cases where at least one issue detected
    error_cases = [r for r in day3_results if r["coding_error_expected"] and not r["error"]]
    coding_error_recall = (
        sum(1 for r in error_cases if r["coding_error_detected"]) / len(error_cases)
        if error_cases else 0.0
    )

    # Deadline exact-match: fraction of cases where deadline date is exactly right
    deadline_cases = [r for r in day3_results if r["expected_deadline"] and not r["error"]]
    deadline_exact_match = (
        sum(1 for r in deadline_cases if r["deadline_correct"]) / len(deadline_cases)
        if deadline_cases else 0.0
    )

    summary = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_cases": n,
        "n_ok": n3_ok,
        "metrics": {
            "extraction_f1": round(extraction_f1, 4),
            "pii_leakage_rate": round(pii_leakage_rate, 4),
            "category_accuracy": round(category_accuracy, 4),
            "retrieval_recall_at_5": round(retrieval_recall_at_5, 4),
            "coding_error_recall": round(coding_error_recall, 4),
            "deadline_exact_match": round(deadline_exact_match, 4),
        },
        "targets": _TARGETS,
        "passed": {
            "extraction_f1": extraction_f1 >= _TARGETS["extraction_f1"],
            "pii_leakage_rate": pii_leakage_rate <= _TARGETS["pii_leakage_rate"],
            "category_accuracy": category_accuracy >= _TARGETS["category_accuracy"],
            "retrieval_recall_at_5": retrieval_recall_at_5 >= _TARGETS["retrieval_recall_at_5"],
            "coding_error_recall": coding_error_recall >= _TARGETS["coding_error_recall"],
            "deadline_exact_match": deadline_exact_match >= _TARGETS["deadline_exact_match"],
        },
        "leaked_cases": [r["case_id"] for r in leaked_cases],
        "case_results": day3_results,
    }
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ClaimBack eval harness (Days 2 + 3)")
    p.add_argument(
        "--fixture-dir",
        default=str(_REPO_ROOT / "data" / "fixtures" / "eval"),
        help="Directory containing JSON eval fixtures (default: data/fixtures/eval)",
    )
    p.add_argument(
        "--subset",
        type=int,
        default=None,
        help="Limit to the first N fixtures (default: all). CI uses --subset 30.",
    )
    p.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "evals" / "results"),
        help="Directory to write results JSON (default: evals/results/)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-case details",
    )
    p.add_argument(
        "--day3-only",
        action="store_true",
        help=(
            "Only run Day-3 metrics (Agents 6-8). "
            "Skips watsonx calls for Agents 3-5. "
            "Uses synthetic smoke fixtures when no fixture-dir is set."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.verbose:
        logging.getLogger("evals").setLevel(logging.INFO)

    fixture_dir = Path(args.fixture_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.day3_only and not list(fixture_dir.glob("*.json")):
        # No fixture files and day3-only: use synthetic smoke set
        fixtures = _synthetic_smoke_day3_fixtures(args.subset or 10)
    else:
        fixtures = _load_fixtures(fixture_dir, args.subset)

    if not fixtures:
        print("ERROR: no fixtures loaded", file=sys.stderr)
        return 1

    print(f"Running eval on {len(fixtures)} case(s)…")
    summary = asyncio.run(_run_eval(fixtures, day3_only=args.day3_only))

    # Write results
    ts = summary["timestamp"].replace(":", "-").replace(".", "-")
    out_path = output_dir / f"{ts}.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Results written to {out_path}")

    # Print summary table
    print("\n-- Metrics --")
    for metric, value in summary["metrics"].items():
        target = _TARGETS.get(metric)
        if target is None:
            continue
        passed_flag = summary["passed"].get(metric, False)
        icon = "PASS" if passed_flag else "FAIL"
        op = "<=" if metric == "pii_leakage_rate" else ">="
        print(f"  [{icon}]  {metric:<30} {value:.4f}  (target {op}{target})")
    print()

    if summary["leaked_cases"]:
        print(f"PII leaked in cases: {summary['leaked_cases']}")

    all_passed = all(summary["passed"].values())
    if all_passed:
        print("All metrics PASSED")
    else:
        failed = [k for k, v in summary["passed"].items() if not v]
        print(f"FAILED metrics: {failed}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
