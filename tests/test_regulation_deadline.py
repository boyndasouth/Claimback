"""Tests for Agent 8 — Regulation & Deadline.

Covers:
  - Standard deadline calculation per state fixture
  - Ohio (no external review)
  - Unknown state → ERISA federal defaults
  - Expedited eligibility: MEDICAL_NECESSITY + billed_amount >= $500
  - Expedited NOT eligible: CODING_ERROR category
  - Expedited NOT eligible: billed_amount < $500
  - Weekend edge case: deadlines land on a Saturday or Sunday (calendar days, no adjustment)
  - Missing denial_date raises ValueError
  - CA expedited with exact $500 threshold
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import CaseContext, DenialCategory, ExtractedClaim


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-008", user_id="u-8", trace_id="t-8")


def _claim(
    *,
    state: str = "CA",
    denial_date: str = "2024-01-01",
    cpt_codes: list[str] | None = None,
    icd10_codes: list[str] | None = None,
    billed_amount: str = "1000.00",
    plan_type: str = "employer",
) -> ExtractedClaim:
    return ExtractedClaim(
        claim_id="CLM-008",
        payer="Aetna",
        plan_type=plan_type,  # type: ignore[arg-type]
        state=state,
        service_date="2024-01-01",
        denial_date=denial_date,
        cpt_codes=cpt_codes or ["99214"],
        icd10_codes=icd10_codes or ["E11.9"],
        billed_amount=Decimal(billed_amount),
        denial_reason_text="Service not medically necessary.",
        denial_reason_code="CO-50",
        field_confidence={},
    )


@pytest.fixture(autouse=True)
def _patch_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    class _AsyncCtx:
        async def __aenter__(self) -> MagicMock:
            return mock_session

        async def __aexit__(self, *a: object) -> bool:
            return False

    monkeypatch.setattr("services.tracing.AsyncSessionFactory", lambda: _AsyncCtx())
    monkeypatch.setattr("services.tracing._get_langfuse", lambda: None)


class TestDeadlineBasicCalculation:
    async def test_ca_internal_deadline_180_days(self) -> None:
        """CA: internal deadline = denial_date + 180 days."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        denial_date = date(2024, 1, 1)
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", denial_date="2024-01-01"),
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.output.internal_appeal_deadline == denial_date + timedelta(days=180)

    async def test_tx_internal_deadline_120_days(self) -> None:
        """TX: internal deadline = denial_date + 120 days."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        denial_date = date(2024, 6, 15)
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="TX", denial_date="2024-06-15"),
                denial_category=DenialCategory.PRIOR_AUTH,
            ),
            _make_ctx(),
        )

        assert result.output.internal_appeal_deadline == denial_date + timedelta(days=120)

    async def test_fl_internal_deadline_365_days(self) -> None:
        """FL: internal deadline = denial_date + 365 days."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        denial_date = date(2024, 3, 1)
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="FL", denial_date="2024-03-01"),
                denial_category=DenialCategory.CODING_ERROR,
            ),
            _make_ctx(),
        )

        assert result.output.internal_appeal_deadline == denial_date + timedelta(days=365)


class TestExternalReview:
    async def test_ca_external_review_available(self) -> None:
        """CA: external review available, deadline = internal + 45 days."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        denial_date = date(2024, 1, 1)
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", denial_date="2024-01-01"),
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.output.external_review_available is True
        # CA: internal_days=180, external_days=45 → denial + 180 + 45
        assert result.output.external_review_deadline == denial_date + timedelta(days=180 + 45)

    async def test_oh_external_review_not_available(self) -> None:
        """OH: external review NOT available; external deadline must be None."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="OH", denial_date="2024-02-01"),
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.output.external_review_available is False
        assert result.output.external_review_deadline is None

    async def test_wa_external_review_20_days(self) -> None:
        """WA: external_days = 20 (shortest in fixture)."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        denial_date = date(2024, 5, 10)
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="WA", denial_date="2024-05-10"),
                denial_category=DenialCategory.OUT_OF_NETWORK,
            ),
            _make_ctx(),
        )

        # WA: internal_days=180, external_days=20
        assert result.output.external_review_deadline == denial_date + timedelta(days=180 + 20)


class TestExpeditedEligibility:
    async def test_medical_necessity_above_threshold_expedited(self) -> None:
        """MEDICAL_NECESSITY + billed_amount >= $500 → expedited_eligible = True."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", billed_amount="2500.00"),
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.output.expedited_eligible is True

    async def test_medical_necessity_exact_threshold_expedited(self) -> None:
        """billed_amount == $500 exactly must be >= threshold → expedited = True."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="NY", billed_amount="500.00"),
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.output.expedited_eligible is True

    async def test_medical_necessity_below_threshold_not_expedited(self) -> None:
        """billed_amount < $500 → expedited_eligible = False even with MEDICAL_NECESSITY."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", billed_amount="499.99"),
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.output.expedited_eligible is False

    async def test_coding_error_not_expedited(self) -> None:
        """CODING_ERROR denial → expedited_eligible = False regardless of amount."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", billed_amount="5000.00"),
                denial_category=DenialCategory.CODING_ERROR,
            ),
            _make_ctx(),
        )

        assert result.output.expedited_eligible is False

    async def test_prior_auth_not_expedited(self) -> None:
        """PRIOR_AUTH denial → expedited_eligible = False."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="TX", billed_amount="10000.00"),
                denial_category=DenialCategory.PRIOR_AUTH,
            ),
            _make_ctx(),
        )

        assert result.output.expedited_eligible is False


class TestUnknownStateERISAFallback:
    async def test_unknown_state_uses_erisa_defaults(self) -> None:
        """State not in fixture → federal ERISA defaults: 180 days, external available, 60 days."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        denial_date = date(2024, 7, 4)
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="ZZ", denial_date="2024-07-04"),  # ZZ not in fixture
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.output.internal_appeal_deadline == denial_date + timedelta(days=180)
        assert result.output.external_review_available is True
        # ERISA: external_days = 60
        assert result.output.external_review_deadline == denial_date + timedelta(days=180 + 60)

    async def test_unknown_state_rule_source_says_erisa(self) -> None:
        """Rule source section must say 'Federal ERISA Default' for unknown states."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="ZZ"),
                denial_category=DenialCategory.ADMINISTRATIVE,
            ),
            _make_ctx(),
        )

        assert any("ERISA" in s.section for s in result.output.rule_sources)


class TestWeekendEdgeCases:
    """Deadlines are calendar days — they MAY land on weekends.

    The spec says no business-day adjustment.  These tests verify the raw
    arithmetic and that the agent does NOT silently push weekends to Monday.
    """

    async def test_deadline_on_saturday_not_adjusted(self) -> None:
        """When internal deadline falls on a Saturday, return Saturday as-is."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        # denial_date + 180 days should land on a specific weekday; we pick a
        # denial_date such that the result is a Saturday (weekday == 5).
        # 2024-01-04 + 180 = 2024-07-02 (Tuesday) — keep incrementing denial to find Saturday.
        # We'll search for a denial_date that makes CA internal deadline a Saturday.
        denial_as_saturday: date | None = None
        for offset in range(7):
            d = date(2024, 1, 1) + timedelta(days=offset)
            deadline = d + timedelta(days=180)
            if deadline.weekday() == 5:  # Saturday
                denial_as_saturday = d
                break

        assert denial_as_saturday is not None, "Could not find a Saturday-landing deadline in test setup"
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", denial_date=denial_as_saturday.isoformat()),
                denial_category=DenialCategory.PRIOR_AUTH,
            ),
            _make_ctx(),
        )
        assert result.output.internal_appeal_deadline.weekday() == 5  # still Saturday

    async def test_deadline_on_sunday_not_adjusted(self) -> None:
        """When internal deadline falls on a Sunday, return Sunday as-is."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        denial_on_sunday: date | None = None
        for offset in range(7):
            d = date(2024, 1, 1) + timedelta(days=offset)
            deadline = d + timedelta(days=180)
            if deadline.weekday() == 6:  # Sunday
                denial_on_sunday = d
                break

        assert denial_on_sunday is not None
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", denial_date=denial_on_sunday.isoformat()),
                denial_category=DenialCategory.PRIOR_AUTH,
            ),
            _make_ctx(),
        )
        assert result.output.internal_appeal_deadline.weekday() == 6  # still Sunday

    async def test_leap_year_february_deadline(self) -> None:
        """Deadline calculation crossing Feb 29 in a leap year must be correct."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        # 2024 is a leap year; denial on 2024-01-10 + 180 days should cross Feb 29
        denial_date = date(2024, 1, 10)
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA", denial_date="2024-01-10"),
                denial_category=DenialCategory.PRIOR_AUTH,
            ),
            _make_ctx(),
        )
        expected = denial_date + timedelta(days=180)
        assert result.output.internal_appeal_deadline == expected


class TestMissingDenialDate:
    async def test_none_denial_date_raises_value_error(self) -> None:
        """denial_date=None must raise ValueError before any date math."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        # Build the claim then patch denial_date to None to simulate missing value
        claim = _claim(state="CA")
        object.__setattr__(claim, "denial_date", None)  # bypass Pydantic validation

        with pytest.raises(ValueError, match="denial_date is required"):
            await agent.run(
                DeadlineInput(claim=claim, denial_category=DenialCategory.MEDICAL_NECESSITY),
                _make_ctx(),
            )


class TestConfidenceAndTokens:
    async def test_deterministic_confidence_is_one(self) -> None:
        """Confidence must be exactly 1.0 — this is deterministic rule-based logic."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="CA"),
                denial_category=DenialCategory.MEDICAL_NECESSITY,
            ),
            _make_ctx(),
        )

        assert result.confidence == 1.0

    async def test_no_llm_tokens_used(self) -> None:
        """tokens_in and tokens_out must both be 0 (no LLM call)."""
        from agents.regulation_deadline import DeadlineInput, RegulationDeadline

        agent = RegulationDeadline()
        result = await agent.run(
            DeadlineInput(
                claim=_claim(state="TX"),
                denial_category=DenialCategory.ADMINISTRATIVE,
            ),
            _make_ctx(),
        )

        assert result.tokens_in == 0
        assert result.tokens_out == 0
