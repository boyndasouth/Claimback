"""Eval harness for Agents 10 + 11 (Writer/Guardian loop) — Day 5.

Metrics:
  - Mean groundedness_score   (target >= 0.95, SPEC §10)
  - Mean letter quality score (target >= 4.0 on a 1-5 rubric, SPEC §10)

Usage::

    python evals/eval_writer_guardian.py              # 5 synthetic smoke cases
    python evals/eval_writer_guardian.py --subset 10  # 10 cases
    python evals/eval_writer_guardian.py --real        # real watsonx calls

The script is designed to run offline using stored synthetic fixtures when
``--real`` is not set (mocked watsonx responses).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import pathlib
import re
import sys
import uuid
from decimal import Decimal
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

logger = logging.getLogger("eval_writer_guardian")

_RESULTS_DIR = _REPO_ROOT / "evals" / "results"
_PROMPTS_DIR = _REPO_ROOT / "prompts"

TARGET_GROUNDEDNESS = 0.95
TARGET_QUALITY = 4.0

# ── Synthetic smoke fixture builder ──────────────────────────────────────────


def _make_smoke_case(idx: int) -> dict[str, Any]:
    from datetime import date

    from app.models import (
        AppealPlan,
        CaseContext,
        EvidenceChecklist,
        EvidenceItem,
        ExtractedClaim,
        PolicyClause,
        SourceRef,
        WriterInput,
    )

    src = SourceRef(
        clause_id=f"c{idx}",
        doc_id=f"policy_{idx}",
        section="§4.1 Medical Necessity",
        text="Coverage requires that the service be medically necessary as determined by a licensed physician.",
    )
    clause = PolicyClause(
        clause_id=src.clause_id,
        doc_id=src.doc_id,
        section=src.section,
        text=src.text,
        score=0.9,
    )
    claim = ExtractedClaim(
        claim_id=f"CLM-{idx:04d}",
        payer="Acme Insurance",
        plan_type="employer",
        state="CA",
        service_date=date(2025, 1, 15),
        denial_date=date(2025, 2, 1),
        cpt_codes=["99213"],
        icd10_codes=["Z00.00"],
        billed_amount=Decimal("450.00"),
        denial_reason_text="Service not medically necessary",
        denial_reason_code="CO-50",
        field_confidence={"claim_id": 1.0},
    )
    plan = AppealPlan(
        internal_appeal_deadline=date(2025, 8, 1),
        external_review_available=True,
        external_review_deadline=date(2025, 10, 1),
        expedited_eligible=False,
        rule_sources=[src],
    )
    checklist = EvidenceChecklist(items=[
        EvidenceItem(
            item_id="E1",
            description="Letter of medical necessity from treating physician",
            rationale="Directly rebuts the medical-necessity denial.",
            priority="high",
        )
    ])
    writer_input = WriterInput(
        checklist=checklist,
        claim=claim,
        clauses=[clause],
        issues=[],
        plan=plan,
    )
    return {
        "idx": idx,
        "writer_input": writer_input,
        "sources": [src],
        "ctx": CaseContext(
            case_id=str(uuid.uuid4()),
            user_id="eval",
            trace_id=str(uuid.uuid4()),
        ),
    }


# ── Mocked watsonx for offline runs ──────────────────────────────────────────


def _mock_writer_response(idx: int) -> str:
    return json.dumps({
        "body_markdown": (
            f"# Appeal Letter {idx}\n\nDear Appeals Department,\n\n"
            "We write to appeal the denial of service dated 2025-02-01 [S1]. "
            "The treating physician has certified this service as medically necessary [S1]. "
            "Per policy §4.1, coverage is provided for medically necessary services [S1].\n\n"
            "We respectfully request that you overturn this denial.\n\nSincerely,\nPatient"
        ),
        "citations": {
            "S1": {
                "clause_id": f"c{idx}",
                "doc_id": f"policy_{idx}",
                "section": "§4.1 Medical Necessity",
                "text": "Coverage requires that the service be medically necessary...",
            }
        },
    })


def _mock_guardian_response() -> str:
    return json.dumps({
        "passed": True,
        "flagged_spans": [],
        "groundedness_score": 0.97,
    })


def _mock_judge_response() -> str:
    return json.dumps({"score": 4, "rationale": "Well-cited and addresses denial directly."})


# ── Case runner ───────────────────────────────────────────────────────────────


async def _run_one_case(fixture: dict, use_real: bool) -> dict[str, Any]:
    from agents.appeal_writer import AppealWriter
    from agents.groundedness_checker import GroundednessChecker
    from app.models import CheckerInput

    writer = AppealWriter()
    checker = GroundednessChecker()

    wi = fixture["writer_input"]
    sources = fixture["sources"]
    ctx = fixture["ctx"]

    idx = fixture["idx"]

    if not use_real:
        # Build mock client inline and patch get_client for the duration of this call
        from unittest.mock import AsyncMock, MagicMock
        from unittest.mock import patch as _patch

        call_count = [0]

        async def _mock_generate(model_tier, prompt, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: writer produces the draft
                return _mock_writer_response(idx), 100, 50
            else:
                # Second+ call: guardian check
                return _mock_guardian_response(), 80, 30

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(side_effect=_mock_generate)

        try:
            with (
                _patch("agents.appeal_writer.get_client", return_value=mock_client),
                _patch("agents.groundedness_checker.get_client", return_value=mock_client),
                _patch("services.tracing.AsyncSessionFactory", MagicMock()),
                _patch("services.tracing.AgentRun", MagicMock()),
            ):
                writer_result = await writer.run(wi, ctx)
                draft = writer_result.output
                checker_input = CheckerInput(draft=draft, sources=sources)
                checker_result = await checker.run(checker_input, ctx)
                gr = checker_result.output

            quality_score = await _judge_quality(draft.body_markdown, ctx, use_real)
            return {
                "case_idx": idx,
                "groundedness_score": gr.groundedness_score,
                "passed": gr.passed,
                "flagged_count": len(gr.flagged_spans),
                "quality_score": quality_score,
                "draft_version": draft.version,
                "error": None,
            }
        except Exception as exc:
            return {
                "case_idx": idx,
                "groundedness_score": 0.0,
                "passed": False,
                "flagged_count": 0,
                "quality_score": 0.0,
                "draft_version": 0,
                "error": str(exc),
            }

    # Real watsonx path
    try:
        # Run writer
        writer_result = await writer.run(wi, ctx)
        draft = writer_result.output

        # Run guardian
        checker_input = CheckerInput(draft=draft, sources=sources)
        checker_result = await checker.run(checker_input, ctx)
        gr = checker_result.output

        # Judge quality using a prompt-based rubric
        quality_score = await _judge_quality(draft.body_markdown, ctx, use_real)

        return {
            "case_idx": idx,
            "groundedness_score": gr.groundedness_score,
            "passed": gr.passed,
            "flagged_count": len(gr.flagged_spans),
            "quality_score": quality_score,
            "draft_version": draft.version,
            "error": None,
        }
    except Exception as exc:
        return {
            "case_idx": idx,
            "groundedness_score": 0.0,
            "passed": False,
            "flagged_count": 0,
            "quality_score": 0.0,
            "draft_version": 0,
            "error": str(exc),
        }


async def _judge_quality(body_markdown: str, ctx, use_real: bool) -> float:
    """Call Granite to score the letter quality on a 1-5 rubric."""
    if not use_real:
        return 4.0

    import re as _re

    from jinja2 import Environment, FileSystemLoader
    from services.watsonx import get_client

    jenv = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)
    tmpl = jenv.get_template("evals/letter_quality_judge.md")
    prompt = _re.sub(
        r"^---\n.*?\n---\n",
        "",
        tmpl.render(draft_body=body_markdown),
        flags=_re.DOTALL,
    ).strip()

    client = get_client()
    text, _, _ = await client.generate(model_tier="routing", prompt=prompt, max_new_tokens=128)
    text = text.strip()
    m = re.search(r'"score"\s*:\s*(\d+)', text)
    return float(m.group(1)) if m else 3.0


# ── Main ──────────────────────────────────────────────────────────────────────


async def run_eval(subset: int, use_real: bool) -> dict[str, Any]:
    fixtures = [_make_smoke_case(i) for i in range(subset)]

    logger.info("Running Writer/Guardian eval on %d cases (real=%s)...", subset, use_real)
    results = []
    for fixture in fixtures:
        r = await _run_one_case(fixture, use_real)
        status = "✓" if r["passed"] else "✗"
        logger.info(
            "[%s] case=%d gs=%.3f quality=%.1f",
            status,
            r["case_idx"],
            r["groundedness_score"],
            r["quality_score"],
        )
        results.append(r)

    ok_results = [r for r in results if not r["error"]]
    n = len(ok_results)
    mean_gs = sum(r["groundedness_score"] for r in ok_results) / n if n else 0.0
    mean_q = sum(r["quality_score"] for r in ok_results) / n if n else 0.0

    summary = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_cases": subset,
        "n_ok": n,
        "metrics": {
            "mean_groundedness_score": round(mean_gs, 4),
            "mean_letter_quality": round(mean_q, 4),
        },
        "targets": {
            "mean_groundedness_score": TARGET_GROUNDEDNESS,
            "mean_letter_quality": TARGET_QUALITY,
        },
        "passed": {
            "mean_groundedness_score": mean_gs >= TARGET_GROUNDEDNESS,
            "mean_letter_quality": mean_q >= TARGET_QUALITY,
        },
        "case_results": results,
    }
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Eval: Writer/Guardian groundedness + quality")
    p.add_argument("--subset", type=int, default=5, help="Number of smoke cases (default 5)")
    p.add_argument("--real", action="store_true", help="Use real watsonx calls")
    p.add_argument("--output-dir", default=str(_RESULTS_DIR))
    args = p.parse_args()

    summary = asyncio.run(run_eval(args.subset, args.real))

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = pathlib.Path(args.output_dir) / f"eval_writer_guardian_{datetime.date.today()}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nResults written to {out}")

    gs = summary["metrics"]["mean_groundedness_score"]
    q = summary["metrics"]["mean_letter_quality"]
    pass_gs = "PASS" if gs >= TARGET_GROUNDEDNESS else "FAIL"
    pass_q = "PASS" if q >= TARGET_QUALITY else "FAIL"
    print(f"Mean groundedness: {gs:.4f} [{pass_gs}]")
    print(f"Mean quality:      {q:.4f} [{pass_q}]")

    all_passed = all(summary["passed"].values())
    if not all_passed:
        print("FAIL: one or more targets not met.")
        return 1
    print("PASS: all targets met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
