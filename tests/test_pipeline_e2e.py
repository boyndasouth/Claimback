"""End-to-end smoke test: one sample case to AWAITING_APPROVAL.

Runs ``_run_pipeline_background`` with all agents mocked and all DB writes
stubbed.  Asserts that ``_set_status`` is eventually called with
``CaseStatus.AWAITING_APPROVAL``.
"""
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
    AgentResult,
    AppealDraft,
    AppealPlan,
    CaseStatus,
    DenialCategory,
    EvidenceChecklist,
    EvidenceItem,
    ExtractedClaim,
    GroundingReport,
    PolicyClause,
    SourceRef,
    SuccessEstimate,
)


def _src() -> SourceRef:
    return SourceRef(clause_id="c1", doc_id="d1", section="§4", text="med necessary")


def _claim() -> ExtractedClaim:
    return ExtractedClaim(
        claim_id="CLM-TEST-001",
        payer="TestPayer",
        plan_type="employer",
        state="CA",
        service_date=date(2025, 1, 1),
        denial_date=date(2025, 2, 1),
        cpt_codes=["99213"],
        icd10_codes=["Z00.00"],
        billed_amount=Decimal("450.00"),
        denial_reason_text="Not medically necessary",
        denial_reason_code="CO-50",
        field_confidence={"claim_id": 1.0},
    )


def _make_ar(output, **kw):
    return AgentResult(
        output=output,
        confidence=0.9,
        sources=[],
        trace_id="t-test",
        tokens_in=10,
        tokens_out=5,
        latency_ms=50,
        **kw,
    )


async def test_pipeline_reaches_awaiting_approval():
    """Full pipeline background task ends at AWAITING_APPROVAL (all agents mocked)."""
    case_id = str(uuid.uuid4())

    # Track all status transitions recorded
    status_history: list[str] = []

    # Build all mock agent responses
    claim = _claim()
    src = _src()
    plan = AppealPlan(
        internal_appeal_deadline=date(2025, 8, 1),
        external_review_available=False,
        external_review_deadline=None,
        expedited_eligible=False,
        rule_sources=[src],
    )
    draft = AppealDraft(
        body_markdown="Appeal [S1]. Medically necessary [S1].",
        citations={"S1": src},
        version=1,
    )
    grounding = GroundingReport(passed=True, flagged_spans=[], groundedness_score=0.98)
    estimate = SuccessEstimate(probability=0.72, top_factors=[], explanation="Good chance.")
    checklist = EvidenceChecklist(items=[
        EvidenceItem(item_id="E1", description="LON", rationale="rebuts", priority="high")
    ])
    clause = PolicyClause(
        clause_id="c1", doc_id="d1", section="§4",
        text="medically necessary", score=0.9,
    )

    # Mock agent instances
    mock_extractor = MagicMock()
    mock_extractor.run = AsyncMock(return_value=_make_ar(claim))
    mock_redactor = MagicMock()
    mock_redactor.run = AsyncMock(return_value=_make_ar(claim))
    mock_classifier = MagicMock()
    mock_classifier.run = AsyncMock(
        return_value=_make_ar((DenialCategory.MEDICAL_NECESSITY, "denial"))
    )
    mock_retriever = MagicMock()
    mock_retriever.run = AsyncMock(return_value=_make_ar([clause]))
    mock_validator = MagicMock()
    mock_validator.run = AsyncMock(return_value=_make_ar([]))
    mock_deadline = MagicMock()
    mock_deadline.run = AsyncMock(return_value=_make_ar(plan))
    mock_eb = MagicMock()
    mock_eb.run = AsyncMock(return_value=_make_ar(checklist))
    mock_aw = MagicMock()
    mock_aw.run = AsyncMock(return_value=_make_ar(draft))
    mock_gc = MagicMock()
    mock_gc.run = AsyncMock(return_value=_make_ar(grounding))
    mock_ss = MagicMock()
    mock_ss.run = AsyncMock(return_value=_make_ar(estimate))
    mock_followup = MagicMock()
    mock_followup.run = AsyncMock(return_value=_make_ar([]))

    # Mock DB session factory — use a plain namespace object so setattr works without recursion
    class _FakeCase:
        def __init__(self):
            self.id = case_id
            self.user_id = "test-user"
            self.category = None
            self.success_prob = None
            self.status = CaseStatus.CREATED

        def __setattr__(self, name, value):
            if name == "status":
                status_history.append(str(value))
            object.__setattr__(self, name, value)

    mock_case_obj = _FakeCase()

    mock_session = AsyncMock()
    mock_query_result = MagicMock()
    mock_query_result.scalar_one_or_none = MagicMock(return_value=mock_case_obj)
    mock_session.execute = AsyncMock(return_value=mock_query_result)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    # Build a mock AnalysisResult to return from run_analysis_stage
    from app.models import AgentResult
    from pipeline.runner import AnalysisResult
    mock_analysis = AnalysisResult(
        policy_clauses=AgentResult(
            output=[clause], confidence=0.9, sources=[src],
            trace_id="t", tokens_in=10, tokens_out=5, latency_ms=100,
        ),
        code_issues=AgentResult(
            output=[], confidence=1.0, sources=[],
            trace_id="t", tokens_in=0, tokens_out=0, latency_ms=10,
        ),
        appeal_plan=AgentResult(
            output=plan, confidence=0.95, sources=[src],
            trace_id="t", tokens_in=20, tokens_out=10, latency_ms=200,
        ),
    )

    with (
        patch("agents.document_extractor.DocumentExtractor", return_value=mock_extractor),
        patch("agents.pii_redactor.PiiRedactor", return_value=mock_redactor),
        patch("agents.denial_classifier.DenialClassifier", return_value=mock_classifier),
        patch("pipeline.runner.run_analysis_stage", AsyncMock(return_value=mock_analysis)),
        patch("agents.followup_tracker.FollowupTracker", return_value=mock_followup),
        patch("pipeline.drafting._get_agents", return_value=(mock_eb, mock_aw, mock_gc, mock_ss)),
        patch("pipeline.drafting.get_cached_result", AsyncMock(return_value=None)),
        patch("pipeline.drafting._persist_draft", AsyncMock()),
        patch("db.AsyncSessionFactory", mock_factory),
        # Suppress tracing DB writes
        patch("services.tracing.AsyncSessionFactory", mock_factory),
        patch("services.tracing.AgentRun", MagicMock()),
    ):
        from app.tools import _run_pipeline_background

        await _run_pipeline_background(
            case_id=case_id,
            user_id="test-user",
            denial_text="Service denied: not medically necessary.",
        )

    # Assert AWAITING_APPROVAL was reached
    assert CaseStatus.AWAITING_APPROVAL in status_history, (
        f"Expected AWAITING_APPROVAL in status transitions but got: {status_history}"
    )
    # Assert the full sequence includes the key transitions
    assert CaseStatus.EXTRACTING in status_history
    assert CaseStatus.ANALYZING in status_history
    # DRAFTING and VERIFYING come from on_status callback (called with CaseStatus enum values)
    # They should appear in the transition log
    assert CaseStatus.AWAITING_APPROVAL == status_history[-1], (
        f"Final status should be AWAITING_APPROVAL but was {status_history[-1]}"
    )
    # Success probability was written
    assert mock_case_obj.success_prob == pytest.approx(0.72, abs=0.01)
