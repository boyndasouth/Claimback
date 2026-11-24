"""Pipeline ANALYZING stage — runs Agents 6, 7, 8 in parallel (SPEC §16).

Usage::

    from pipeline.runner import run_analysis_stage, AnalysisResult
    result = await run_analysis_stage(claim, category, ctx)

``asyncio.gather`` fires all three agents concurrently; the first exception
propagates (fail-fast).  Callers that need partial results should wrap in
try/except per agent instead.
"""
from __future__ import annotations

import asyncio
import dataclasses

from agents.code_validator import CodeValidator
from agents.policy_retriever import PolicyRetriever
from agents.regulation_deadline import DeadlineInput, RegulationDeadline
from app.models import (
    AgentResult,
    AppealPlan,
    CaseContext,
    CodeIssue,
    DenialCategory,
    ExtractedClaim,
    PolicyClause,
)

# ── Module-level agent singletons (instantiated once, reused across calls) ────

_retriever = PolicyRetriever()
_validator = CodeValidator()
_deadline_agent = RegulationDeadline()


@dataclasses.dataclass
class AnalysisResult:
    """Container for the three parallel agent results."""

    policy_clauses: AgentResult[list[PolicyClause]]
    code_issues: AgentResult[list[CodeIssue]]
    appeal_plan: AgentResult[AppealPlan]


async def run_analysis_stage(
    claim: ExtractedClaim,
    category: DenialCategory,
    ctx: CaseContext,
) -> AnalysisResult:
    """Run Agents 6, 7, 8 in parallel and return an AnalysisResult.

    Parameters
    ----------
    claim:
        Redacted claim from Agent 4 (typed as ExtractedClaim).
    category:
        Denial category from Agent 5.
    ctx:
        Ambient case context (case_id, user_id, trace_id).

    Returns
    -------
    AnalysisResult
        Fields populated from Agent 6, 7, 8 outputs respectively.

    Raises
    ------
    Any exception raised by an agent propagates immediately (asyncio.gather
    default behaviour with return_exceptions=False).
    """
    deadline_input = DeadlineInput(claim=claim, denial_category=category)

    clauses_result, issues_result, plan_result = await asyncio.gather(
        _retriever.run(claim, ctx),
        _validator.run(claim, ctx),
        _deadline_agent.run(deadline_input, ctx),
    )

    return AnalysisResult(
        policy_clauses=clauses_result,
        code_issues=issues_result,
        appeal_plan=plan_result,
    )
