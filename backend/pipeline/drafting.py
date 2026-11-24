"""Pipeline DRAFTING stage — Agents 9, 10, 11 (loop), 12 (§5.2, §16).

Implements the Writer ⇄ Guardian self-correction loop with a hard cap of
:data:`MAX_REWRITES` iterations.  Status transitions are signalled through the
``on_status`` callback so the caller (``_run_pipeline_background``) remains in
control of DB writes and SSE events.

Usage::

    from pipeline.drafting import run_drafting_stage, NeedsHumanReview, DraftingResult

    try:
        result = await run_drafting_stage(
            analysis=analysis_result,
            claim=redacted_claim,
            ctx=ctx,
            on_status=_on_status,
        )
    except NeedsHumanReview:
        await _set_status(CaseStatus.NEEDS_HUMAN_REVIEW)
        return
"""
from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.models import (
    AppealDraft,
    AppealPlan,
    CaseContext,
    CaseStatus,
    CheckerInput,
    EvidenceBuilderInput,
    EvidenceChecklist,
    ExtractedClaim,
    FlaggedSpan,
    GroundingReport,
    SourceRef,
    SuccessEstimate,
    SuccessScorerInput,
    WriterInput,
    WriterRetryInput,
)
from pipeline.runner import AnalysisResult
from services.cache import get_cached_result, hash_input

logger = logging.getLogger(__name__)

MAX_REWRITES = 2  # SPEC §5.2

StatusCallback = Callable[[CaseStatus, dict[str, Any]], Awaitable[None]]

# ── Module-level agent singletons ─────────────────────────────────────────────

_evidence_builder = None
_appeal_writer = None
_groundedness_checker = None
_success_scorer = None


def _get_agents() -> tuple[Any, Any, Any, Any]:
    global _evidence_builder, _appeal_writer, _groundedness_checker, _success_scorer
    if _evidence_builder is None:
        from agents.evidence_builder import EvidenceBuilder
        from agents.appeal_writer import AppealWriter
        from agents.groundedness_checker import GroundednessChecker
        from agents.success_scorer import SuccessScorer
        _evidence_builder = EvidenceBuilder()
        _appeal_writer = AppealWriter()
        _groundedness_checker = GroundednessChecker()
        _success_scorer = SuccessScorer()
    return _evidence_builder, _appeal_writer, _groundedness_checker, _success_scorer


# ── Result container ──────────────────────────────────────────────────────────


@dataclasses.dataclass
class DraftingResult:
    """Container for the DRAFTING stage outputs."""

    checklist: EvidenceChecklist
    final_draft: AppealDraft
    grounding_report: GroundingReport
    success_estimate: SuccessEstimate
    rewrite_count: int


class NeedsHumanReview(Exception):
    """Raised when the Writer/Guardian loop exhausts MAX_REWRITES."""

    def __init__(self, flagged_spans: list[FlaggedSpan], draft: AppealDraft) -> None:
        super().__init__(
            f"Appeal draft failed groundedness check after {MAX_REWRITES} rewrites."
        )
        self.flagged_spans = flagged_spans
        self.draft = draft


# ── Draft persistence helper ──────────────────────────────────────────────────


async def _persist_draft(
    case_id: str,
    draft: AppealDraft,
    grounding_report: GroundingReport | None,
) -> None:
    """Insert or update the Draft row for ``(case_id, draft.version)``.

    Called twice per iteration — before the groundedness check (report None)
    and after it. Previously each call inserted a new row, leaving two rows with
    the same version; "latest draft" (ORDER BY version DESC) then often returned
    the one with a null report. Now the second call updates the first row.

    The JSONB also carries the draft's citations (with source text), which the
    draft API reads from ``grounding_report["citations"]`` for citation hovers.
    """
    from sqlalchemy import select

    from db import AsyncSessionFactory
    from db.models import Draft

    report: dict[str, Any] = (
        grounding_report.model_dump(mode="json")
        if grounding_report
        else {"passed": None, "flagged_spans": [], "pending": True}
    )
    report["citations"] = {
        key: src.model_dump(mode="json") for key, src in draft.citations.items()
    }

    async with AsyncSessionFactory() as session:
        row = (
            await session.execute(
                select(Draft).where(
                    Draft.case_id == case_id, Draft.version == draft.version
                )
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                Draft(
                    id=str(uuid.uuid4()),
                    case_id=case_id,
                    version=draft.version,
                    body=draft.body_markdown,
                    grounding_report=report,
                )
            )
        else:
            row.body = draft.body_markdown
            row.grounding_report = report
        await session.commit()


# ── Main stage runner ─────────────────────────────────────────────────────────


async def run_drafting_stage(
    analysis: AnalysisResult,
    claim: ExtractedClaim,
    ctx: CaseContext,
    on_status: StatusCallback,
    category: Any = None,
) -> DraftingResult:
    """Run the DRAFTING stage: Agents 9 → 10 ⇄ 11 → 12.

    Parameters
    ----------
    analysis:
        Result of the ANALYZING stage (Agents 6, 7, 8).
    claim:
        Redacted extracted claim.
    ctx:
        Ambient case context.
    on_status:
        Async callback ``(CaseStatus, detail_dict) -> None``.  Called with
        ``DRAFTING`` before each Writer pass and ``VERIFYING`` before each
        Guardian pass, carrying ``{"iteration": n}``.

    Returns
    -------
    DraftingResult
        Contains the final draft, grounding report, and success estimate.

    Raises
    ------
    NeedsHumanReview
        If the loop exhausts :data:`MAX_REWRITES` without a passing draft.
    """
    eb, aw, gc, ss = _get_agents()

    clauses = analysis.policy_clauses.output
    issues = analysis.code_issues.output
    appeal_plan: AppealPlan = analysis.appeal_plan.output

    # ── Step 1: Evidence Builder (Agent 9) ────────────────────────────────────
    from app.models import DenialCategory
    _category: DenialCategory = category or DenialCategory.ADMINISTRATIVE
    eb_input = EvidenceBuilderInput(
        category=_category,
        clauses=clauses,
        issues=issues,
    )
    _eb_hash = hash_input(eb_input)
    _cached = await get_cached_result(ctx.case_id, "evidence_builder", _eb_hash)
    if _cached:
        checklist = EvidenceChecklist.model_validate(_cached.get("output", _cached))
        logger.debug("Cache hit for evidence_builder")
    else:
        eb_result = await eb.run(eb_input, ctx)
        checklist = eb_result.output

    # ── Step 2: Writer ⇄ Guardian loop ────────────────────────────────────────
    writer_input: WriterInput | WriterRetryInput = WriterInput(
        checklist=checklist,
        claim=claim,
        clauses=clauses,
        issues=issues,
        plan=appeal_plan,
    )
    draft: AppealDraft | None = None
    grounding_report: GroundingReport | None = None
    rewrite_count = 0

    for n in range(MAX_REWRITES + 1):
        # Signal: Writer pass starting
        await on_status(CaseStatus.DRAFTING, {"iteration": n})

        # Writer (Agent 10)
        _w_hash = hash_input(writer_input)
        _cached_w = await get_cached_result(ctx.case_id, "appeal_writer", _w_hash)
        if _cached_w:
            draft = AppealDraft.model_validate(_cached_w.get("output", _cached_w))
            logger.debug("Cache hit for appeal_writer iteration %d", n)
        else:
            aw_result = await aw.run(writer_input, ctx)
            draft = aw_result.output

        # Persist this draft version
        await _persist_draft(ctx.case_id, draft, grounding_report=None)

        # Signal: Guardian check starting
        await on_status(CaseStatus.VERIFYING, {"iteration": n})

        # Guardian (Agent 11)
        all_sources: list[SourceRef] = list(draft.citations.values())
        checker_input = CheckerInput(draft=draft, sources=all_sources)
        _gc_hash = hash_input(checker_input)
        _cached_gc = await get_cached_result(ctx.case_id, "groundedness_checker", _gc_hash)
        if _cached_gc:
            grounding_report = GroundingReport.model_validate(
                _cached_gc.get("output", _cached_gc)
            )
            logger.debug("Cache hit for groundedness_checker iteration %d", n)
        else:
            gc_result = await gc.run(checker_input, ctx)
            grounding_report = gc_result.output

        # Update draft in DB with grounding report
        await _persist_draft(ctx.case_id, draft, grounding_report)

        if grounding_report.passed:
            break

        # Failed — check if we have rewrites left
        if n == MAX_REWRITES:
            raise NeedsHumanReview(
                flagged_spans=grounding_report.flagged_spans,
                draft=draft,
            )

        # Build retry input for next iteration
        writer_input = WriterRetryInput(
            base=writer_input if isinstance(writer_input, WriterInput) else writer_input.base,
            flagged_spans=grounding_report.flagged_spans,
            previous_draft=draft,
        )
        rewrite_count += 1

    assert draft is not None
    assert grounding_report is not None

    # ── Step 3: Success Scorer (Agent 12) ─────────────────────────────────────
    scorer_input = SuccessScorerInput(
        category=_category,
        billed_amount=float(claim.billed_amount),
        state=claim.state,
        plan_type=claim.plan_type,
        code_issues_count=len(issues),
        expedited_eligible=appeal_plan.expedited_eligible,
    )
    _ss_hash = hash_input(scorer_input)
    _cached_ss = await get_cached_result(ctx.case_id, "success_scorer", _ss_hash)
    if _cached_ss:
        success_estimate = SuccessEstimate.model_validate(_cached_ss.get("output", _cached_ss))
        logger.debug("Cache hit for success_scorer")
    else:
        ss_result = await ss.run(scorer_input, ctx)
        success_estimate = ss_result.output

    return DraftingResult(
        checklist=checklist,
        final_draft=draft,
        grounding_report=grounding_report,
        success_estimate=success_estimate,
        rewrite_count=rewrite_count,
    )
