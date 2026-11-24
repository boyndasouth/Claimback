"""Tests for the Day-3 pipeline runner (asyncio.gather for agents 6/7/8)."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from app.models import (
    AgentResult,
    AppealPlan,
    CaseContext,
    CodeIssue,
    DenialCategory,
    ExtractedClaim,
    PolicyClause,
    SourceRef,
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-runner", user_id="u-r", trace_id="t-r")


def _claim() -> ExtractedClaim:
    return ExtractedClaim(
        claim_id="CLM-R",
        payer="Aetna",
        plan_type="employer",
        state="CA",
        service_date="2024-01-01",
        denial_date="2024-01-15",
        cpt_codes=["99214"],
        icd10_codes=["E11.9"],
        billed_amount=Decimal("1000.00"),
        denial_reason_text="Not medically necessary.",
        denial_reason_code="CO-50",
        field_confidence={},
    )


def _make_policy_result() -> AgentResult[list[PolicyClause]]:
    return AgentResult(
        output=[
            PolicyClause(clause_id="c1", doc_id="d1", section="S1", text="Coverage applies.", score=0.9)
        ],
        confidence=0.9,
        sources=[SourceRef(clause_id="c1", doc_id="d1", section="S1", text="Coverage.")],
        trace_id="t-r",
        tokens_in=0,
        tokens_out=0,
        latency_ms=50,
    )


def _make_issues_result() -> AgentResult[list[CodeIssue]]:
    return AgentResult(
        output=[],
        confidence=1.0,
        sources=[],
        trace_id="t-r",
        tokens_in=0,
        tokens_out=0,
        latency_ms=5,
    )


def _make_plan_result() -> AgentResult[AppealPlan]:
    plan = AppealPlan(
        internal_appeal_deadline=date(2024, 7, 14),
        external_review_available=True,
        external_review_deadline=date(2024, 8, 28),
        expedited_eligible=True,
        rule_sources=[SourceRef(clause_id="CA", doc_id="state_rules_fixture", section="California", text="180 days.")],
    )
    return AgentResult(
        output=plan,
        confidence=1.0,
        sources=plan.rule_sources,
        trace_id="t-r",
        tokens_in=0,
        tokens_out=0,
        latency_ms=2,
    )


class TestRunnerOutputStructure:
    async def test_returns_analysis_result_dataclass(self) -> None:
        """run_analysis_stage must return an AnalysisResult with all three fields."""
        from pipeline.runner import AnalysisResult, run_analysis_stage

        policy_res = _make_policy_result()
        issues_res = _make_issues_result()
        plan_res = _make_plan_result()

        with (
            patch("pipeline.runner._retriever") as mock_retriever,
            patch("pipeline.runner._validator") as mock_validator,
            patch("pipeline.runner._deadline_agent") as mock_deadline,
        ):
            mock_retriever.run = AsyncMock(return_value=policy_res)
            mock_validator.run = AsyncMock(return_value=issues_res)
            mock_deadline.run = AsyncMock(return_value=plan_res)

            result = await run_analysis_stage(_claim(), DenialCategory.MEDICAL_NECESSITY, _make_ctx())

        assert isinstance(result, AnalysisResult)
        assert result.policy_clauses is policy_res
        assert result.code_issues is issues_res
        assert result.appeal_plan is plan_res

    async def test_policy_clauses_field_type(self) -> None:
        """policy_clauses field must be AgentResult[list[PolicyClause]]."""
        from pipeline.runner import run_analysis_stage

        with (
            patch("pipeline.runner._retriever") as mock_retriever,
            patch("pipeline.runner._validator") as mock_validator,
            patch("pipeline.runner._deadline_agent") as mock_deadline,
        ):
            mock_retriever.run = AsyncMock(return_value=_make_policy_result())
            mock_validator.run = AsyncMock(return_value=_make_issues_result())
            mock_deadline.run = AsyncMock(return_value=_make_plan_result())

            result = await run_analysis_stage(_claim(), DenialCategory.PRIOR_AUTH, _make_ctx())

        assert isinstance(result.policy_clauses.output, list)
        assert all(isinstance(c, PolicyClause) for c in result.policy_clauses.output)


class TestRunnerParallelExecution:
    async def test_all_three_agents_called(self) -> None:
        """All three agent run() methods must be called exactly once per invocation."""
        from pipeline.runner import run_analysis_stage

        retriever_calls: list[int] = []
        validator_calls: list[int] = []
        deadline_calls: list[int] = []

        async def _retriever_run(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            retriever_calls.append(1)
            return _make_policy_result()

        async def _validator_run(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            validator_calls.append(1)
            return _make_issues_result()

        async def _deadline_run(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            deadline_calls.append(1)
            return _make_plan_result()

        with (
            patch("pipeline.runner._retriever") as mock_retriever,
            patch("pipeline.runner._validator") as mock_validator,
            patch("pipeline.runner._deadline_agent") as mock_deadline,
        ):
            mock_retriever.run = _retriever_run
            mock_validator.run = _validator_run
            mock_deadline.run = _deadline_run

            await run_analysis_stage(_claim(), DenialCategory.MEDICAL_NECESSITY, _make_ctx())

        assert len(retriever_calls) == 1
        assert len(validator_calls) == 1
        assert len(deadline_calls) == 1

    async def test_agents_run_concurrently(self) -> None:
        """Agents must run via asyncio.gather — verify by timing overlapping sleeps.

        Each mock agent sleeps 0.05s.  Sequential would take >= 0.15s.
        Concurrent should take < 0.15s (allowing generous margin).
        """
        from pipeline.runner import run_analysis_stage

        async def _slow_retriever(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            await asyncio.sleep(0.05)
            return _make_policy_result()

        async def _slow_validator(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            await asyncio.sleep(0.05)
            return _make_issues_result()

        async def _slow_deadline(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            await asyncio.sleep(0.05)
            return _make_plan_result()

        import time

        with (
            patch("pipeline.runner._retriever") as mock_retriever,
            patch("pipeline.runner._validator") as mock_validator,
            patch("pipeline.runner._deadline_agent") as mock_deadline,
        ):
            mock_retriever.run = _slow_retriever
            mock_validator.run = _slow_validator
            mock_deadline.run = _slow_deadline

            t0 = time.monotonic()
            await run_analysis_stage(_claim(), DenialCategory.CODING_ERROR, _make_ctx())
            elapsed = time.monotonic() - t0

        # Sequential minimum would be 0.15s; concurrent should finish in ~0.05s.
        # Allow generous 0.12s to avoid flakiness on slow CI runners.
        assert elapsed < 0.12, f"Agents ran sequentially (elapsed={elapsed:.3f}s >= 0.12s)"

    async def test_agent_exception_propagates(self) -> None:
        """If one agent raises, the exception must propagate from run_analysis_stage."""
        from pipeline.runner import run_analysis_stage

        async def _exploding_run(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            raise RuntimeError("retriever down")

        with (
            patch("pipeline.runner._retriever") as mock_retriever,
            patch("pipeline.runner._validator") as mock_validator,
            patch("pipeline.runner._deadline_agent") as mock_deadline,
        ):
            mock_retriever.run = _exploding_run
            mock_validator.run = AsyncMock(return_value=_make_issues_result())
            mock_deadline.run = AsyncMock(return_value=_make_plan_result())

            with pytest.raises(RuntimeError, match="retriever down"):
                await run_analysis_stage(_claim(), DenialCategory.MEDICAL_NECESSITY, _make_ctx())


class TestRunnerDeadlineInput:
    async def test_deadline_input_carries_category(self) -> None:
        """Agent 8 must receive a DeadlineInput with the correct denial_category."""
        from agents.regulation_deadline import DeadlineInput
        from pipeline.runner import run_analysis_stage

        received_inputs: list[DeadlineInput] = []

        async def _capture_deadline(input: object, ctx: object) -> AgentResult:  # type: ignore[type-arg]
            if isinstance(input, DeadlineInput):
                received_inputs.append(input)
            return _make_plan_result()

        with (
            patch("pipeline.runner._retriever") as mock_retriever,
            patch("pipeline.runner._validator") as mock_validator,
            patch("pipeline.runner._deadline_agent") as mock_deadline,
        ):
            mock_retriever.run = AsyncMock(return_value=_make_policy_result())
            mock_validator.run = AsyncMock(return_value=_make_issues_result())
            mock_deadline.run = _capture_deadline

            await run_analysis_stage(_claim(), DenialCategory.OUT_OF_NETWORK, _make_ctx())

        assert len(received_inputs) == 1
        assert received_inputs[0].denial_category == DenialCategory.OUT_OF_NETWORK
