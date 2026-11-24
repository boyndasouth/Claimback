"""Unit tests for Pydantic models (§6) and the synthetic data generator (§9)."""
from __future__ import annotations

import pathlib
import sys
from datetime import date
from decimal import Decimal

import pytest

# Ensure backend is on the path
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import (
    AgentResult,
    AppealDraft,
    AppealPlan,
    CaseContext,
    CaseStatus,
    DenialCategory,
    ExtractedClaim,
    FlaggedSpan,
    GroundingReport,
    PolicyClause,
    SourceRef,
    SuccessEstimate,
)

# ---------------------------------------------------------------------------
# Base / shared model tests
# ---------------------------------------------------------------------------

class TestSourceRef:
    def test_round_trip(self) -> None:
        s = SourceRef(clause_id="c1", doc_id="d1", section="§3.2", text="must obtain PA")
        assert SourceRef.model_validate(s.model_dump()) == s

    def test_required_fields(self) -> None:
        with pytest.raises(Exception):
            SourceRef(clause_id="x")  # type: ignore[call-arg]


class TestFlaggedSpan:
    def test_construction(self) -> None:
        fs = FlaggedSpan(span="the drug is safe", reason="unsupported claim")
        assert fs.span == "the drug is safe"
        assert fs.reason == "unsupported claim"


class TestCaseContext:
    def test_fields(self) -> None:
        ctx = CaseContext(case_id="abc", user_id="u1", trace_id="t1")
        assert ctx.case_id == "abc"


# ---------------------------------------------------------------------------
# CaseStatus enum
# ---------------------------------------------------------------------------

class TestCaseStatus:
    def test_all_values_present(self) -> None:
        expected = {
            "created", "extracting", "analyzing", "drafting", "verifying",
            "needs_human_review", "awaiting_approval", "ready_to_send",
            "follow_up", "external_review", "closed",
        }
        actual = {s.value for s in CaseStatus}
        assert actual == expected

    def test_is_str(self) -> None:
        assert isinstance(CaseStatus.CREATED, str)
        assert CaseStatus.CREATED == "created"


# ---------------------------------------------------------------------------
# DenialCategory enum
# ---------------------------------------------------------------------------

class TestDenialCategory:
    def test_all_categories_present(self) -> None:
        values = {c.value for c in DenialCategory}
        assert "medical_necessity" in values
        assert "prior_authorization" in values
        assert "coding_error" in values
        assert "out_of_network" in values
        assert "not_covered_benefit" in values
        assert "administrative" in values

    def test_count(self) -> None:
        assert len(DenialCategory) == 6


# ---------------------------------------------------------------------------
# ExtractedClaim
# ---------------------------------------------------------------------------

class TestExtractedClaim:
    def _valid(self) -> dict:
        return {
            "claim_id": "CLM-001",
            "payer": "BluePeak",
            "plan_type": "employer",
            "state": "CA",
            "service_date": date(2025, 3, 1),
            "denial_date": date(2025, 3, 20),
            "cpt_codes": ["72148"],
            "icd10_codes": ["M54.50"],
            "billed_amount": Decimal("1250.00"),
            "denial_reason_text": "Not medically necessary.",
            "denial_reason_code": "CO-50",
            "field_confidence": {"claim_id": 0.99},
        }

    def test_valid_construction(self) -> None:
        claim = ExtractedClaim(**self._valid())
        assert claim.claim_id == "CLM-001"
        assert claim.billed_amount == Decimal("1250.00")

    def test_plan_type_literal(self) -> None:
        with pytest.raises(Exception):
            d = self._valid()
            d["plan_type"] = "dental"
            ExtractedClaim(**d)

    def test_denial_reason_code_optional(self) -> None:
        d = self._valid()
        d["denial_reason_code"] = None
        claim = ExtractedClaim(**d)
        assert claim.denial_reason_code is None

    def test_state_is_str(self) -> None:
        claim = ExtractedClaim(**self._valid())
        assert len(claim.state) == 2

    def test_serialization_roundtrip(self) -> None:
        claim = ExtractedClaim(**self._valid())
        restored = ExtractedClaim.model_validate_json(claim.model_dump_json())
        assert restored == claim

    def test_plan_types_all_accepted(self) -> None:
        for pt in ("employer", "marketplace", "medicaid", "medicare_advantage"):
            d = self._valid()
            d["plan_type"] = pt
            assert ExtractedClaim(**d).plan_type == pt


# ---------------------------------------------------------------------------
# PolicyClause
# ---------------------------------------------------------------------------

class TestPolicyClause:
    def test_construction(self) -> None:
        pc = PolicyClause(
            clause_id="cl1", doc_id="d1", section="§2.4", text="PA required.", score=0.92
        )
        assert pc.score == 0.92


# ---------------------------------------------------------------------------
# AppealPlan
# ---------------------------------------------------------------------------

class TestAppealPlan:
    def test_no_external(self) -> None:
        ap = AppealPlan(
            internal_appeal_deadline=date(2025, 6, 1),
            external_review_available=False,
            external_review_deadline=None,
            expedited_eligible=False,
            rule_sources=[],
        )
        assert ap.external_review_deadline is None

    def test_with_external(self) -> None:
        s = SourceRef(clause_id="c", doc_id="d", section="§9", text="ext review")
        ap = AppealPlan(
            internal_appeal_deadline=date(2025, 6, 1),
            external_review_available=True,
            external_review_deadline=date(2025, 7, 15),
            expedited_eligible=True,
            rule_sources=[s],
        )
        assert ap.external_review_deadline == date(2025, 7, 15)
        assert len(ap.rule_sources) == 1


# ---------------------------------------------------------------------------
# AppealDraft
# ---------------------------------------------------------------------------

class TestAppealDraft:
    def test_citations_dict(self) -> None:
        s = SourceRef(clause_id="c", doc_id="d", section="§1", text="text")
        draft = AppealDraft(
            body_markdown="See [S1] for details.",
            citations={"S1": s},
            version=1,
        )
        assert "S1" in draft.citations


# ---------------------------------------------------------------------------
# GroundingReport
# ---------------------------------------------------------------------------

class TestGroundingReport:
    def test_passed(self) -> None:
        gr = GroundingReport(passed=True, flagged_spans=[], groundedness_score=1.0)
        assert gr.passed

    def test_failed(self) -> None:
        fs = FlaggedSpan(span="bad claim", reason="no citation")
        gr = GroundingReport(passed=False, flagged_spans=[fs], groundedness_score=0.5)
        assert not gr.passed
        assert len(gr.flagged_spans) == 1


# ---------------------------------------------------------------------------
# SuccessEstimate
# ---------------------------------------------------------------------------

class TestSuccessEstimate:
    def test_construction(self) -> None:
        se = SuccessEstimate(
            probability=0.72,
            top_factors=[("coding_error_present", 0.3), ("no_prior_auth", -0.1)],
            explanation="Coding error cases resolve at higher rates.",
        )
        assert se.probability == 0.72
        assert len(se.top_factors) == 2


# ---------------------------------------------------------------------------
# AgentResult (Generic)
# ---------------------------------------------------------------------------

class TestAgentResult:
    def test_generic_with_str(self) -> None:
        s = SourceRef(clause_id="c", doc_id="d", section="s", text="t")
        result: AgentResult[str] = AgentResult(
            output="hello",
            confidence=0.9,
            sources=[s],
            trace_id="tr1",
            tokens_in=100,
            tokens_out=50,
            latency_ms=342,
        )
        assert result.output == "hello"
        assert result.tokens_in == 100

    def test_generic_with_model(self) -> None:
        se = SuccessEstimate(probability=0.6, top_factors=[], explanation="ok")
        s = SourceRef(clause_id="c", doc_id="d", section="s", text="t")
        result: AgentResult[SuccessEstimate] = AgentResult(
            output=se,
            confidence=0.8,
            sources=[s],
            trace_id="tr2",
            tokens_in=200,
            tokens_out=80,
            latency_ms=1000,
        )
        assert result.output.probability == 0.6


# ---------------------------------------------------------------------------
# Day-5 models
# ---------------------------------------------------------------------------



from app.models import (
    EvidenceChecklist,
    EvidenceItem,
    SuccessScorerInput,
)


class TestEvidenceChecklist:
    def test_round_trip(self) -> None:
        item = EvidenceItem(
            item_id="E1",
            description="LON",
            rationale="rebuts denial",
            priority="high",
        )
        cl = EvidenceChecklist(items=[item])
        assert cl.items[0].item_id == "E1"
        assert EvidenceChecklist.model_validate(cl.model_dump()) == cl

    def test_invalid_priority_raises(self) -> None:
        with pytest.raises(Exception):
            EvidenceItem(item_id="E1", description="x", rationale="x", priority="critical")  # type: ignore


class TestSuccessScorerInput:
    def test_construction(self) -> None:
        inp = SuccessScorerInput(
            category=DenialCategory.CODING_ERROR,
            billed_amount=850.0,
            state="TX",
            plan_type="employer",
            code_issues_count=2,
            expedited_eligible=True,
        )
        assert inp.category == DenialCategory.CODING_ERROR
        assert inp.expedited_eligible is True

