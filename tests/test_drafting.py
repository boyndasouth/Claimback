"""Unit tests for pipeline/drafting.py — Writer/Guardian loop logic."""
from __future__ import annotations

import pathlib
import sys
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import (
    AppealDraft,
    AppealPlan,
    CaseContext,
    CaseStatus,
    EvidenceChecklist,
    EvidenceItem,
    ExtractedClaim,
    FlaggedSpan,
    GroundingReport,
    PolicyClause,
    SourceRef,
    SuccessEstimate,
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id=str(uuid.uuid4()), user_id="u1", trace_id="t1")


def _make_analysis_result():
    """Build a minimal AnalysisResult for testing."""
    from app.models import AgentResult
    from pipeline.runner import AnalysisResult

    src = SourceRef(clause_id="c1", doc_id="d1", section="§4", text="medically necessary")
    clause = PolicyClause(clause_id="c1", doc_id="d1", section="§4", text="medically necessary", score=0.9)
    plan = AppealPlan(
        internal_appeal_deadline=date(2025, 8, 1),
        external_review_available=False,
        external_review_deadline=None,
        expedited_eligible=False,
        rule_sources=[src],
    )
    return AnalysisResult(
        policy_clauses=AgentResult(
            output=[clause], confidence=0.9, sources=[src],
            trace_id="t1", tokens_in=10, tokens_out=5, latency_ms=100,
        ),
        code_issues=AgentResult(
            output=[], confidence=1.0, sources=[],
            trace_id="t1", tokens_in=0, tokens_out=0, latency_ms=10,
        ),
        appeal_plan=AgentResult(
            output=plan, confidence=0.95, sources=[src],
            trace_id="t1", tokens_in=20, tokens_out=10, latency_ms=200,
        ),
    )


def _make_claim() -> ExtractedClaim:
    return ExtractedClaim(
        claim_id="CLM-001",
        payer="Acme",
        plan_type="employer",
        state="CA",
        service_date=date(2025, 1, 1),
        denial_date=date(2025, 2, 1),
        cpt_codes=["99213"],
        icd10_codes=["Z00.00"],
        billed_amount=Decimal("450.00"),
        denial_reason_text="Not medically necessary",
        denial_reason_code="CO-50",
        field_confidence={},
    )


def _make_good_draft(version: int = 1) -> AppealDraft:
    src = SourceRef(clause_id="c1", doc_id="d1", section="§4", text="medically necessary")
    return AppealDraft(
        body_markdown="Appeal [S1]. Medically necessary [S1].",
        citations={"S1": src},
        version=version,
    )


def _make_good_grounding() -> GroundingReport:
    return GroundingReport(passed=True, flagged_spans=[], groundedness_score=0.98)


def _make_failing_grounding() -> GroundingReport:
    return GroundingReport(
        passed=False,
        flagged_spans=[FlaggedSpan(span="unsupported claim", reason="no citation")],
        groundedness_score=0.5,
    )


def _make_success_estimate() -> SuccessEstimate:
    return SuccessEstimate(probability=0.72, top_factors=[], explanation="Good chance.")


def _make_checklist() -> EvidenceChecklist:
    return EvidenceChecklist(items=[
        EvidenceItem(item_id="E1", description="LON", rationale="rebuts", priority="high")
    ])


class TestDraftingStageLoop:
    def test_clean_pass_calls_callback_once_draft_once_verify(self) -> None:
        """Clean pass: exactly [(DRAFTING,0), (VERIFYING,0)] callback calls."""
        from app.models import AgentResult
        from pipeline.drafting import DraftingResult, run_drafting_stage

        status_calls = []

        async def fake_callback(status, detail):
            status_calls.append((status, detail.get("iteration")))

        # Patch all agents and DB operations
        mock_eb = MagicMock()
        mock_eb.run = AsyncMock(return_value=AgentResult(
            output=_make_checklist(), confidence=0.9, sources=[],
            trace_id="t", tokens_in=10, tokens_out=5, latency_ms=50,
        ))
        mock_aw = MagicMock()
        mock_aw.run = AsyncMock(return_value=AgentResult(
            output=_make_good_draft(), confidence=0.8, sources=[],
            trace_id="t", tokens_in=100, tokens_out=50, latency_ms=500,
        ))
        mock_gc = MagicMock()
        mock_gc.run = AsyncMock(return_value=AgentResult(
            output=_make_good_grounding(), confidence=0.98, sources=[],
            trace_id="t", tokens_in=80, tokens_out=30, latency_ms=400,
        ))
        mock_ss = MagicMock()
        mock_ss.run = AsyncMock(return_value=AgentResult(
            output=_make_success_estimate(), confidence=0.72, sources=[],
            trace_id="t", tokens_in=50, tokens_out=25, latency_ms=300,
        ))

        import asyncio

        with (
            patch("pipeline.drafting._get_agents", return_value=(mock_eb, mock_aw, mock_gc, mock_ss)),
            patch("pipeline.drafting.get_cached_result", AsyncMock(return_value=None)),
            patch("pipeline.drafting._persist_draft", AsyncMock()),
        ):
            analysis = _make_analysis_result()
            claim = _make_claim()
            ctx = _make_ctx()
            result = asyncio.run(
                run_drafting_stage(analysis, claim, ctx, fake_callback)
            )

        assert isinstance(result, DraftingResult)
        assert result.rewrite_count == 0
        assert status_calls == [(CaseStatus.DRAFTING, 0), (CaseStatus.VERIFYING, 0)]

    def test_one_rewrite_callback_sequence(self) -> None:
        """One rewrite: [(DRAFTING,0), (VERIFYING,0), (DRAFTING,1), (VERIFYING,1)]."""
        from app.models import AgentResult
        from pipeline.drafting import run_drafting_stage

        status_calls = []

        async def fake_callback(status, detail):
            status_calls.append((status, detail.get("iteration")))

        draft_v1 = _make_good_draft(version=1)
        draft_v2 = _make_good_draft(version=2)

        gc_call_count = [0]

        async def mock_gc_run(inp, ctx):
            gc_call_count[0] += 1
            if gc_call_count[0] == 1:
                report = _make_failing_grounding()
            else:
                report = _make_good_grounding()
            return AgentResult(
                output=report, confidence=0.9, sources=[],
                trace_id="t", tokens_in=80, tokens_out=30, latency_ms=400,
            )

        aw_call_count = [0]

        async def mock_aw_run(inp, ctx):
            aw_call_count[0] += 1
            draft = draft_v1 if aw_call_count[0] == 1 else draft_v2
            return AgentResult(
                output=draft, confidence=0.8, sources=[],
                trace_id="t", tokens_in=100, tokens_out=50, latency_ms=500,
            )

        mock_eb = MagicMock()
        mock_eb.run = AsyncMock(return_value=AgentResult(
            output=_make_checklist(), confidence=0.9, sources=[],
            trace_id="t", tokens_in=10, tokens_out=5, latency_ms=50,
        ))
        mock_aw = MagicMock()
        mock_aw.run = AsyncMock(side_effect=mock_aw_run)
        mock_gc = MagicMock()
        mock_gc.run = AsyncMock(side_effect=mock_gc_run)
        mock_ss = MagicMock()
        mock_ss.run = AsyncMock(return_value=AgentResult(
            output=_make_success_estimate(), confidence=0.72, sources=[],
            trace_id="t", tokens_in=50, tokens_out=25, latency_ms=300,
        ))

        import asyncio

        with (
            patch("pipeline.drafting._get_agents", return_value=(mock_eb, mock_aw, mock_gc, mock_ss)),
            patch("pipeline.drafting.get_cached_result", AsyncMock(return_value=None)),
            patch("pipeline.drafting._persist_draft", AsyncMock()),
        ):
            analysis = _make_analysis_result()
            claim = _make_claim()
            ctx = _make_ctx()
            result = asyncio.run(
                run_drafting_stage(analysis, claim, ctx, fake_callback)
            )

        assert result.rewrite_count == 1
        assert status_calls == [
            (CaseStatus.DRAFTING, 0),
            (CaseStatus.VERIFYING, 0),
            (CaseStatus.DRAFTING, 1),
            (CaseStatus.VERIFYING, 1),
        ]

    def test_needs_human_review_raised_after_max_rewrites(self) -> None:
        """After MAX_REWRITES Guardian still fails -> NeedsHumanReview."""
        from app.models import AgentResult
        from pipeline.drafting import NeedsHumanReview, run_drafting_stage

        async def fake_callback(status, detail):
            pass

        mock_eb = MagicMock()
        mock_eb.run = AsyncMock(return_value=AgentResult(
            output=_make_checklist(), confidence=0.9, sources=[],
            trace_id="t", tokens_in=10, tokens_out=5, latency_ms=50,
        ))
        mock_aw = MagicMock()
        mock_aw.run = AsyncMock(return_value=AgentResult(
            output=_make_good_draft(), confidence=0.8, sources=[],
            trace_id="t", tokens_in=100, tokens_out=50, latency_ms=500,
        ))
        mock_gc = MagicMock()
        mock_gc.run = AsyncMock(return_value=AgentResult(
            output=_make_failing_grounding(), confidence=0.5, sources=[],
            trace_id="t", tokens_in=80, tokens_out=30, latency_ms=400,
        ))
        mock_ss = MagicMock()
        mock_ss.run = AsyncMock(return_value=AgentResult(
            output=_make_success_estimate(), confidence=0.72, sources=[],
            trace_id="t", tokens_in=50, tokens_out=25, latency_ms=300,
        ))

        import asyncio

        with (
            patch("pipeline.drafting._get_agents", return_value=(mock_eb, mock_aw, mock_gc, mock_ss)),
            patch("pipeline.drafting.get_cached_result", AsyncMock(return_value=None)),
            patch("pipeline.drafting._persist_draft", AsyncMock()),
        ):
            analysis = _make_analysis_result()
            claim = _make_claim()
            ctx = _make_ctx()
            with pytest.raises(NeedsHumanReview):
                asyncio.run(
                    run_drafting_stage(analysis, claim, ctx, fake_callback)
                )


# ── _persist_draft: one row per version, report + citations saved ─────────────


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """Minimal async session backed by a dict keyed on (case_id, version)."""

    store: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        params = stmt.compile().params
        key = tuple(v for k, v in sorted(params.items()))
        return _FakeResult(self.store.get(key))

    def add(self, row):
        self.store[(row.case_id, row.version)] = row

    async def commit(self):
        pass


async def test_persist_draft_updates_same_version_with_report_and_citations():
    from app.models import AppealDraft, GroundingReport, SourceRef
    from pipeline.drafting import _persist_draft

    _FakeSession.store = {}
    src = SourceRef(clause_id="c1", doc_id="d1", section="2.2 Diagnostic Imaging", text="MRI requires PA.")
    draft = AppealDraft(body_markdown="Body [S1]", citations={"S1": src}, version=1)
    report = GroundingReport(passed=True, flagged_spans=[], groundedness_score=1.0)

    with patch("db.AsyncSessionFactory", _FakeSession):
        await _persist_draft("case-1", draft, grounding_report=None)
        await _persist_draft("case-1", draft, grounding_report=report)

    assert len(_FakeSession.store) == 1, "second call must update, not insert a duplicate"
    row = next(iter(_FakeSession.store.values()))
    assert row.grounding_report["passed"] is True
    assert row.grounding_report["citations"]["S1"]["section"] == "2.2 Diagnostic Imaging"
    assert "pending" not in row.grounding_report
