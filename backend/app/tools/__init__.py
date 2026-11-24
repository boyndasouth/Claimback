"""Orchestrate-facing /tools router (§7.2).

All seven operations are protected by the ``verify_api_key`` dependency which
checks the ``X-API-Key`` header against the ``TOOLS_API_KEY`` env var.

A ``GET /tools/openapi.json`` endpoint returns a filtered OpenAPI spec slice
containing only the ``/tools/*`` paths — used by the Day-4 Orchestrate import
script.

Day-5 wiring:
- ``_run_pipeline_background`` now advances through EXTRACTING → ANALYZING →
  DRAFTING → VERIFYING → AWAITING_APPROVAL (or NEEDS_HUMAN_REVIEW).
- ``case_get_draft`` returns the latest draft body + grounding verdict.
- ``case_approve`` marks draft approved and sets status READY_TO_SEND.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CaseContext, CaseStatus
from db import get_session
from db.models import Case, Draft, Reminder

from .auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tools"])

# ── Category -> plain-language map ────────────────────────────────────────────

_CATEGORY_EXPLANATIONS: dict[str, str] = {
    "medical_necessity": (
        "Your insurer determined that the service was not medically necessary "
        "under their coverage criteria, even though your doctor ordered it. "
        "This category is frequently overturned on appeal when supporting "
        "clinical documentation is provided."
    ),
    "prior_authorization": (
        "The service required advance approval (prior authorisation) from your "
        "insurer before it was provided, and that approval was not obtained. "
        "Appeals often succeed if the service was urgent or the requirement "
        "was not clearly communicated."
    ),
    "coding_error": (
        "The claim was denied because of a mismatch or error in the billing "
        "codes (CPT or ICD-10). This is typically a correctable administrative "
        "issue — a corrected claim or a letter from the provider's billing "
        "department is usually sufficient."
    ),
    "out_of_network": (
        "The service was provided by a provider who is not in your insurance "
        "network. Depending on your plan and the circumstances, you may still "
        "be able to appeal, especially if no in-network provider was available."
    ),
    "not_covered_benefit": (
        "The service is specifically excluded from your plan's benefits. "
        "Appeals are possible if the exclusion is ambiguous or if the service "
        "can be characterised differently (e.g. as a different covered benefit)."
    ),
    "administrative": (
        "The claim was denied for an administrative reason such as missing "
        "information, a timely-filing deadline violation, or a duplicate "
        "submission. These are often the easiest denials to fix by resubmitting "
        "with the correct information."
    ),
}

# ── Request / Response schemas ────────────────────────────────────────────────


class CaseCreateRequest(BaseModel):
    user_id: str
    notes: str = ""


class CaseCreateResponse(BaseModel):
    case_id: str
    status: str


class CaseStatusResponse(BaseModel):
    case_id: str
    status: str
    summary: str | None = None


class RunPipelineRequest(BaseModel):
    case_id: str
    denial_text: str = ""


class GetExplanationResponse(BaseModel):
    case_id: str
    explanation: str
    category: str | None = None


class GetDraftResponse(BaseModel):
    case_id: str
    draft: str | None = None
    grounding_passed: bool | None = None
    version: int | None = None


class ApproveRequest(BaseModel):
    case_id: str
    approved_by: str


class ScheduleFollowupRequest(BaseModel):
    case_id: str
    due_at: str  # ISO-8601 datetime string
    kind: str = "internal_appeal"


# ── Background pipeline runner ────────────────────────────────────────────────


async def _run_pipeline_background(case_id: str, user_id: str, denial_text: str) -> None:
    """Background task: agents 3->4->5 -> run_analysis_stage -> run_drafting_stage.

    Status transitions (all via on_status callback or direct _set_status):
        CREATED -> EXTRACTING -> ANALYZING -> DRAFTING -> VERIFYING
        -> AWAITING_APPROVAL  (success)
        -> NEEDS_HUMAN_REVIEW (Guardian loop exhausted or any unhandled exception)

    Any exception is caught and logged; the case status is set to
    NEEDS_HUMAN_REVIEW so the UI can surface it.
    """
    from agents.denial_classifier import DenialClassifier
    from agents.document_extractor import DocumentExtractor
    from agents.followup_tracker import FollowupTracker
    from agents.pii_redactor import PiiRedactor
    from db import AsyncSessionFactory
    from pipeline.drafting import NeedsHumanReview, run_drafting_stage
    from pipeline.runner import run_analysis_stage

    _extractor = DocumentExtractor()
    _redactor = PiiRedactor()
    _classifier = DenialClassifier()
    _followup = FollowupTracker()

    ctx = CaseContext(case_id=case_id, user_id=user_id, trace_id=str(uuid.uuid4()))

    async def _set_status(new_status: str) -> None:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Case).where(Case.id == case_id))
            case = result.scalar_one_or_none()
            if case is not None:
                case.status = new_status
                await session.commit()

    async def _on_status(new_status: CaseStatus, detail: dict) -> None:
        """Status callback passed into run_drafting_stage."""
        await _set_status(new_status)
        logger.info(
            "Pipeline status: case=%s status=%s detail=%s",
            case_id,
            new_status,
            detail,
        )
        # SSE full wiring is in /api router; placeholder logged above.

    try:
        # EXTRACTING stage
        await _set_status(CaseStatus.EXTRACTING)
        extract_result = await _extractor.run(denial_text or "no document provided", ctx)
        extracted_claim = extract_result.output

        # Redact PII
        redact_result = await _redactor.run(extracted_claim, ctx)
        redacted_claim = redact_result.output

        # Classify denial
        classify_result = await _classifier.run(redacted_claim, ctx)
        category, _rationale = classify_result.output

        # Persist category on the case row
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Case).where(Case.id == case_id))
            case = result.scalar_one_or_none()
            if case is not None:
                case.category = str(category)
                await session.commit()

        # ANALYZING stage (Agents 6, 7, 8 in parallel)
        await _set_status(CaseStatus.ANALYZING)
        analysis_result = await run_analysis_stage(redacted_claim, category, ctx)

        # DRAFTING stage (Agents 9->10<->11->12)
        # on_status callback handles DRAFTING / VERIFYING transitions
        try:
            draft_result = await run_drafting_stage(
                analysis=analysis_result,
                claim=redacted_claim,
                ctx=ctx,
                on_status=_on_status,
                category=category,
            )
        except NeedsHumanReview:
            await _set_status(CaseStatus.NEEDS_HUMAN_REVIEW)
            return

        # Persist success probability
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Case).where(Case.id == case_id))
            case = result.scalar_one_or_none()
            if case is not None:
                case.success_prob = draft_result.success_estimate.probability
                await session.commit()

        await _set_status(CaseStatus.AWAITING_APPROVAL)

        # Agent 13: Follow-up Tracker
        try:
            await _followup.run(analysis_result.appeal_plan.output, ctx)
        except Exception as exc:
            logger.warning("FollowupTracker failed (non-fatal): %s", exc)

    except Exception as exc:
        logger.exception(
            "Pipeline background task failed for case %s: %s", case_id, exc
        )
        await _set_status(CaseStatus.NEEDS_HUMAN_REVIEW)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/case_create",
    operation_id="case_create",
    response_model=CaseCreateResponse,
    summary="Create a new case from chat",
)
async def case_create(
    body: CaseCreateRequest,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_session),
) -> CaseCreateResponse:
    """Insert a new case row and return its UUID."""
    case = Case(
        id=str(uuid.uuid4()),
        user_id=body.user_id,
        status=CaseStatus.CREATED,
    )
    session.add(case)
    await session.commit()
    return CaseCreateResponse(case_id=case.id, status=case.status)


@router.get(
    "/case_status",
    operation_id="case_status",
    response_model=CaseStatusResponse,
    summary="Return case status and summary",
)
async def case_status(
    case_id: str,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_session),
) -> CaseStatusResponse:
    """Read the case row and return current status plus a one-line summary."""
    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    summary_parts: list[str] = [f"Status: {case.status}"]
    if case.category:
        summary_parts.append(f"Denial category: {case.category}")
    if case.success_prob is not None:
        summary_parts.append(f"Success probability: {case.success_prob:.0%}")
    summary = " · ".join(summary_parts)

    return CaseStatusResponse(case_id=case.id, status=case.status, summary=summary)


@router.post(
    "/case_run_pipeline",
    operation_id="case_run_pipeline",
    summary="Kick off the analysis pipeline for a case",
)
async def case_run_pipeline(
    body: RunPipelineRequest,
    background_tasks: BackgroundTasks,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue agents 3->4->5 -> analysis -> drafting stage as a background task.

    Returns immediately with ``{"status": "pipeline_started", "case_id": ...}``.
    The case status transitions from CREATED -> EXTRACTING -> ANALYZING ->
    DRAFTING -> VERIFYING -> AWAITING_APPROVAL in the background.
    Poll ``case_status`` to track progress.
    """
    result = await session.execute(select(Case).where(Case.id == body.case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    background_tasks.add_task(
        _run_pipeline_background,
        case_id=case.id,
        user_id=case.user_id,
        denial_text=body.denial_text,
    )
    return {"status": "pipeline_started", "case_id": case.id}


@router.get(
    "/case_get_explanation",
    operation_id="case_get_explanation",
    response_model=GetExplanationResponse,
    summary="Return a plain-language denial explanation",
)
async def case_get_explanation(
    case_id: str,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_session),
) -> GetExplanationResponse:
    """Return the denial category and plain-language explanation via get_case_summary.

    Uses the same service as GET /api/cases/{id}/summary so chat and UI always agree.
    """
    from services.case_summary import get_case_summary

    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    summary = await get_case_summary(case_id, session)
    if summary and summary.explanation:
        return GetExplanationResponse(
            case_id=case_id,
            explanation=summary.explanation,
            category=case.category,
        )

    # Fallback: use static category map if agent_runs not yet populated
    if not case.category:
        return GetExplanationResponse(
            case_id=case_id,
            explanation=(
                "The analysis pipeline has not completed yet. "
                f"Current status: {case.status}. "
                "Please run the pipeline or check back in a moment."
            ),
            category=None,
        )

    explanation = _CATEGORY_EXPLANATIONS.get(
        case.category,
        f"Denial category: {case.category}. No detailed explanation is available yet.",
    )
    return GetExplanationResponse(
        case_id=case_id,
        explanation=explanation,
        category=case.category,
    )


@router.get(
    "/case_get_draft",
    operation_id="case_get_draft",
    response_model=GetDraftResponse,
    summary="Return current appeal draft and grounding report",
)
async def case_get_draft(
    case_id: str,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_session),
) -> GetDraftResponse:
    """Return the latest appeal draft body and grounding verdict."""
    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    draft_result = await session.execute(
        select(Draft)
        .where(Draft.case_id == case_id)
        .order_by(desc(Draft.version))
        .limit(1)
    )
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        return GetDraftResponse(case_id=case_id, draft=None, grounding_passed=None)

    grounding_passed: bool | None = None
    if draft.grounding_report:
        grounding_passed = bool(draft.grounding_report.get("passed"))

    return GetDraftResponse(
        case_id=case_id,
        draft=draft.body,
        grounding_passed=grounding_passed,
        version=draft.version,
    )


@router.post(
    "/case_approve",
    operation_id="case_approve",
    summary="Approve the appeal draft (called from Orchestrate flow)",
)
async def case_approve(
    body: ApproveRequest,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Record approval; advance case status to READY_TO_SEND."""
    case_result = await session.execute(select(Case).where(Case.id == body.case_id))
    case = case_result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    draft_result = await session.execute(
        select(Draft)
        .where(Draft.case_id == body.case_id)
        .order_by(desc(Draft.version))
        .limit(1)
    )
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No draft found for this case",
        )

    draft.approved_by = body.approved_by
    draft.approved_at = datetime.now(timezone.utc)
    case.status = CaseStatus.READY_TO_SEND
    await session.commit()
    return {"status": "approved", "case_id": body.case_id}


@router.post(
    "/case_schedule_followup",
    operation_id="case_schedule_followup",
    summary="Create a follow-up reminder for a case",
)
async def case_schedule_followup(
    body: ScheduleFollowupRequest,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Insert a Reminder row for the case."""
    result = await session.execute(select(Case).where(Case.id == body.case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    try:
        due_at = datetime.fromisoformat(body.due_at)
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"due_at must be a valid ISO-8601 datetime: {exc}",
        ) from exc

    reminder = Reminder(
        case_id=body.case_id,
        due_at=due_at,
        kind=body.kind,
        sent=False,
    )
    session.add(reminder)
    await session.commit()
    return {"status": "reminder_created", "case_id": body.case_id}


# ── OpenAPI export ────────────────────────────────────────────────────────────


@router.get(
    "/openapi.json",
    include_in_schema=False,
    summary="OpenAPI spec slice for /tools — imported by Orchestrate on Day 4",
)
async def tools_openapi(request: Request) -> dict[str, Any]:
    """Return an OpenAPI 3.1 spec containing only the /tools/* paths."""
    app = request.app
    full_spec: dict[str, Any] = app.openapi()

    tools_paths = {
        path: item
        for path, item in full_spec.get("paths", {}).items()
        if path.startswith("/tools/") and not path.endswith("/openapi.json")
    }

    return {
        "openapi": full_spec.get("openapi", "3.1.0"),
        "info": {
            **full_spec.get("info", {}),
            "title": "ClaimBack Tools API",
        },
        "paths": tools_paths,
        "components": full_spec.get("components", {}),
    }
