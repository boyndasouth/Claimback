"""Frontend API router — /api endpoints (§7.1).

Implements all routes:
  POST   /api/cases                       — create case
  POST   /api/cases/{id}/run              — start pipeline (async)
  GET    /api/cases/{id}/events           — SSE agent-progress stream
  GET    /api/cases/{id}                  — full case state
  GET    /api/cases/{id}/summary          — CaseSummary (progressive fill-in)
  GET    /api/cases/{id}/draft            — latest AppealDraft + GroundingReport
  PATCH  /api/cases/{id}/draft            — save user edits (synchronous re-verify)
  POST   /api/cases/{id}/approve          — human approval
  GET    /api/cases/{id}/trace            — per-agent trace timeline
  GET    /api/evals/latest                — latest eval run summary
  GET    /api/replay/                     — list available replay fixtures
  GET    /api/replay/{case_id}            — serve a recorded trace fixture
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CaseContext, CaseStatus
from db import get_session
from db.models import AgentRun, Case, Draft
from services.rate_limiter import check_rate_limit, retry_after_seconds

logger = logging.getLogger(__name__)

router = APIRouter()

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
_EVALS_RESULTS = _REPO_ROOT / "evals" / "results"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    """Return the real client IP, honouring X-Forwarded-For (Render proxy).

    Takes the first entry of the X-Forwarded-For header when present, otherwise
    falls back to ``request.client.host``.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Request / Response schemas ────────────────────────────────────────────────


class CreateCaseRequest(BaseModel):
    user_id: str
    notes: str = ""


class CreateCaseResponse(BaseModel):
    case_id: str
    status: str


class RunPipelineRequest(BaseModel):
    denial_text: str = ""


class CaseStateResponse(BaseModel):
    case_id: str
    status: str
    category: str | None = None
    success_prob: float | None = None
    draft: str | None = None
    draft_version: int | None = None
    grounding_passed: bool | None = None


class PatchDraftRequest(BaseModel):
    body_markdown: str
    approved_by: str = ""


class ApproveRequest(BaseModel):
    approved_by: str


class DraftResponse(BaseModel):
    """Response for GET /api/cases/{id}/draft and PATCH /api/cases/{id}/draft."""

    case_id: str
    version: int
    body_markdown: str
    citations: dict[str, Any]
    grounding_report: dict[str, Any] | None = None


class TraceEvent(BaseModel):
    agent: str
    input_hash: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float | None
    trace_id: str


# ── SSE helpers ───────────────────────────────────────────────────────────────

# In-memory broadcast registry: case_id -> list of asyncio.Queue
# Each SSE connection gets its own Queue.  When status changes, all queues for
# that case receive the event.  This is intentionally simple — Day 7 can swap
# to Redis pub/sub for multi-worker deployments.
_sse_queues: dict[str, list[asyncio.Queue]] = {}


def _broadcast(case_id: str, event: dict[str, Any]) -> None:
    """Push an SSE event to all connected clients for this case."""
    queues = _sse_queues.get(case_id, [])
    for q in queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow consumer — drop event


def broadcast_status(case_id: str, new_status: str, detail: dict | None = None) -> None:
    """Public helper for the pipeline background task to emit SSE events."""
    _broadcast(case_id, {"type": "status", "status": new_status, **(detail or {})})


_TERMINAL = {
    CaseStatus.AWAITING_APPROVAL,
    CaseStatus.NEEDS_HUMAN_REVIEW,
    CaseStatus.READY_TO_SEND,
    CaseStatus.CLOSED,
}


async def _sse_generator(case_id: str, request: Request, current_status: str | None = None):
    """Async generator that yields SSE-formatted strings.

    The case's current status is sent first, so a client that connects late
    (page refresh, or after the pipeline already started) isn't left waiting
    for events that were broadcast before it subscribed. If the case is
    already finished, that first event is the last.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _sse_queues.setdefault(case_id, []).append(q)
    try:
        if current_status:
            yield f"data: {json.dumps({'type': 'status', 'status': str(current_status)})}\n\n"
            if current_status in _TERMINAL:
                return
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                payload = json.dumps(event)
                yield f"data: {payload}\n\n"
                # Terminal events: close the stream
                if event.get("status") in _TERMINAL:
                    break
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        queues = _sse_queues.get(case_id, [])
        if q in queues:
            queues.remove(q)


# ── Background pipeline with SSE broadcasting ─────────────────────────────────


async def _run_pipeline_with_sse(case_id: str, user_id: str, denial_text: str) -> None:
    """Wraps _run_pipeline_background and broadcasts status changes via SSE."""
    # Import the pipeline runner components locally to intercept on_status.
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
        broadcast_status(case_id, new_status)

    async def _on_status(new_status: CaseStatus, detail: dict) -> None:
        await _set_status(new_status)
        _broadcast(case_id, {"type": "status", "status": str(new_status), **detail})

    try:
        await _set_status(CaseStatus.EXTRACTING)
        extract_result = await _extractor.run(denial_text or "no document provided", ctx)
        extracted_claim = extract_result.output

        redact_result = await _redactor.run(extracted_claim, ctx)
        redacted_claim = redact_result.output

        classify_result = await _classifier.run(redacted_claim, ctx)
        category, _rationale = classify_result.output

        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Case).where(Case.id == case_id))
            case = result.scalar_one_or_none()
            if case is not None:
                case.category = str(category)
                await session.commit()

        await _set_status(CaseStatus.ANALYZING)
        analysis_result = await run_analysis_stage(redacted_claim, category, ctx)

        try:
            draft_result = await run_drafting_stage(
                analysis=analysis_result,
                claim=redacted_claim,
                ctx=ctx,
                on_status=_on_status,
                category=category,
            )
        except NeedsHumanReview as exc:
            await _set_status(CaseStatus.NEEDS_HUMAN_REVIEW)
            _broadcast(case_id, {
                "type": "needs_review",
                "flagged_count": len(exc.flagged_spans),
            })
            return

        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Case).where(Case.id == case_id))
            case = result.scalar_one_or_none()
            if case is not None:
                case.success_prob = draft_result.success_estimate.probability
                await session.commit()

        await _set_status(CaseStatus.AWAITING_APPROVAL)
        _broadcast(case_id, {
            "type": "done",
            "success_prob": draft_result.success_estimate.probability,
            "rewrite_count": draft_result.rewrite_count,
        })

        try:
            await _followup.run(analysis_result.appeal_plan.output, ctx)
        except Exception as exc:
            logger.warning("FollowupTracker failed (non-fatal): %s", exc)

    except Exception as exc:
        logger.exception("Pipeline failed for case %s: %s", case_id, exc)
        await _set_status(CaseStatus.NEEDS_HUMAN_REVIEW)
        _broadcast(case_id, {"type": "error", "message": str(exc)})


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/cases",
    operation_id="api_cases_create",
    response_model=CreateCaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new case",
)
async def api_cases_create(
    request: Request,
    body: CreateCaseRequest,
    session: AsyncSession = Depends(get_session),
) -> CreateCaseResponse:
    """Insert a new case row and return its UUID."""
    client_ip = _client_ip(request)
    if not check_rate_limit(client_ip, limit=30, window_seconds=3600):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded — 30 case creations per hour per IP",
            headers={"Retry-After": str(retry_after_seconds(client_ip))},
        )
    case = Case(
        id=str(uuid.uuid4()),
        user_id=body.user_id,
        status=CaseStatus.CREATED,
    )
    session.add(case)
    await session.commit()
    return CreateCaseResponse(case_id=case.id, status=case.status)


@router.post(
    "/cases/{case_id}/run",
    operation_id="api_cases_run",
    summary="Start the analysis + drafting pipeline (async)",
)
async def api_cases_run(
    case_id: str,
    request: Request,
    body: RunPipelineRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue the full pipeline as a background task.

    Returns immediately; subscribe to ``GET /api/cases/{id}/events`` for
    real-time SSE progress.
    """
    client_ip = _client_ip(request)
    if not check_rate_limit(client_ip, limit=20, window_seconds=3600):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded — 20 pipeline runs per hour per IP",
            headers={"Retry-After": str(retry_after_seconds(client_ip))},
        )
    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    background_tasks.add_task(
        _run_pipeline_with_sse,
        case_id=case.id,
        user_id=case.user_id,
        denial_text=body.denial_text,
    )
    return {"status": "pipeline_started", "case_id": case.id}


@router.get(
    "/cases/{case_id}/events",
    operation_id="api_cases_events",
    summary="SSE stream of agent progress events",
    response_class=StreamingResponse,
)
async def api_cases_events(
    case_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Server-Sent Events stream.

    Each event is a JSON object with at least a ``type`` and ``status`` field.
    The stream closes after a terminal status (AWAITING_APPROVAL or
    NEEDS_HUMAN_REVIEW) is emitted, or after a 15-second keepalive timeout
    with no events.
    """
    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    return StreamingResponse(
        _sse_generator(case_id, request, current_status=case.status),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/cases/{case_id}",
    operation_id="api_cases_get",
    response_model=CaseStateResponse,
    summary="Full case state",
)
async def api_cases_get(
    case_id: str,
    session: AsyncSession = Depends(get_session),
) -> CaseStateResponse:
    """Return full case state including latest draft."""
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

    grounding_passed: bool | None = None
    if draft and draft.grounding_report:
        grounding_passed = bool(draft.grounding_report.get("passed"))

    return CaseStateResponse(
        case_id=case.id,
        status=case.status,
        category=case.category,
        success_prob=case.success_prob,
        draft=draft.body if draft else None,
        draft_version=draft.version if draft else None,
        grounding_passed=grounding_passed,
    )


@router.get(
    "/cases/{case_id}/summary",
    operation_id="api_cases_summary",
    summary="CaseSummary — denial explanation, deadlines, evidence, code issues, success estimate",
)
async def api_cases_summary(
    case_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return typed CaseSummary assembled from agent_runs output.

    Fields are null until their producing agent has written to agent_runs.
    Returns HTTP 404 if the case does not exist.
    """
    from services.case_summary import get_case_summary

    summary = await get_case_summary(case_id, session)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return summary.model_dump()


@router.get(
    "/cases/{case_id}/draft",
    operation_id="api_cases_get_draft",
    response_model=DraftResponse,
    summary="Latest AppealDraft body, citations, and GroundingReport",
)
async def api_cases_get_draft(
    case_id: str,
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Return the latest draft including citations and grounding report."""
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No draft found for this case"
        )

    # Extract citations from the grounding_report JSONB if present
    citations: dict[str, Any] = {}
    if draft.grounding_report and isinstance(draft.grounding_report, dict):
        citations = draft.grounding_report.get("citations", {})

    return DraftResponse(
        case_id=case_id,
        version=draft.version,
        body_markdown=draft.body,
        citations=citations,
        grounding_report=draft.grounding_report,
    )


def _build_draft_response(case_id: str, draft: Draft) -> DraftResponse:
    """Build a DraftResponse from a Draft ORM row."""
    citations: dict[str, Any] = {}
    if draft.grounding_report and isinstance(draft.grounding_report, dict):
        citations = draft.grounding_report.get("citations", {})
    return DraftResponse(
        case_id=case_id,
        version=draft.version,
        body_markdown=draft.body,
        citations=citations,
        grounding_report=draft.grounding_report,
    )


@router.patch(
    "/cases/{case_id}/draft",
    operation_id="api_cases_patch_draft",
    response_model=DraftResponse,
    summary="Save user edits to the draft (synchronous re-verify, returns updated DraftResponse)",
)
async def api_cases_patch_draft(
    case_id: str,
    body: PatchDraftRequest,
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Save an edited draft body, await re-verification, and return the updated DraftResponse.

    The PATCH is synchronous: it awaits the Groundedness Checker before returning so
    the editor can update citation hovers and flagged spans immediately.
    """
    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    # Read latest draft to inherit existing citations
    draft_result = await session.execute(
        select(Draft)
        .where(Draft.case_id == case_id)
        .order_by(desc(Draft.version))
        .limit(1)
    )
    existing_draft = draft_result.scalar_one_or_none()
    new_version = (existing_draft.version + 1) if existing_draft else 1
    inherited_citations: dict[str, Any] = {}
    if existing_draft and isinstance(existing_draft.grounding_report, dict):
        inherited_citations = existing_draft.grounding_report.get("citations") or {}

    # Persist user-edited draft; citations carry over so hovers and the
    # re-check still have the original sources.
    new_draft = Draft(
        id=str(uuid.uuid4()),
        case_id=case_id,
        version=new_version,
        body=body.body_markdown,
        grounding_report={
            "passed": None,
            "flagged_spans": [],
            "pending": True,
            "citations": inherited_citations,
        },
    )
    session.add(new_draft)
    case.status = CaseStatus.VERIFYING
    await session.commit()

    # Synchronously re-run Guardian and update the draft row
    await _reverify_draft(
        case_id=case_id,
        draft_id=new_draft.id,
        body_markdown=body.body_markdown,
        citations_json=inherited_citations,
    )

    # Reload draft to get persisted grounding_report
    await session.refresh(new_draft)
    return _build_draft_response(case_id, new_draft)


async def _reverify_draft(
    case_id: str,
    draft_id: str,
    body_markdown: str,
    citations_json: dict[str, Any] | None = None,
) -> None:
    """Synchronously re-run the Groundedness Checker and persist the result.

    The edited draft is checked against the citations inherited from the
    previous version; checking against no sources would flag every sentence.
    """
    from sqlalchemy import update

    from agents.groundedness_checker import GroundednessChecker
    from app.models import AppealDraft, CheckerInput, SourceRef
    from db import AsyncSessionFactory
    from db.models import Draft as DraftModel

    ctx = CaseContext(
        case_id=case_id,
        user_id="system",
        trace_id=str(uuid.uuid4()),
    )
    checker = GroundednessChecker()
    citations: dict[str, SourceRef] = {}
    for key, raw in (citations_json or {}).items():
        try:
            citations[key] = SourceRef.model_validate(raw)
        except Exception:  # noqa: BLE001 — skip malformed legacy entries
            logger.warning("Skipping malformed citation %s on case %s", key, case_id)
    draft_obj = AppealDraft(
        body_markdown=body_markdown,
        citations=citations,
        version=0,
    )
    checker_input = CheckerInput(draft=draft_obj, sources=list(citations.values()))
    try:
        result = await checker.run(checker_input, ctx)
        gr = result.output

        async with AsyncSessionFactory() as session:
            stmt = (
                update(DraftModel)
                .where(DraftModel.id == draft_id)
                .values(
                    grounding_report={
                        **gr.model_dump(mode="json"),
                        "citations": {
                            k: v.model_dump(mode="json") for k, v in citations.items()
                        },
                    }
                )
            )
            await session.execute(stmt)
            case_result = await session.execute(select(Case).where(Case.id == case_id))
            case = case_result.scalar_one_or_none()
            if case:
                case.status = (
                    CaseStatus.AWAITING_APPROVAL if gr.passed else CaseStatus.NEEDS_HUMAN_REVIEW
                )
            await session.commit()
        broadcast_status(case_id, case.status if case else CaseStatus.NEEDS_HUMAN_REVIEW)
    except Exception as exc:
        logger.exception("Re-verification failed for case %s: %s", case_id, exc)


@router.post(
    "/cases/{case_id}/approve",
    operation_id="api_cases_approve",
    summary="Human approval of the appeal draft",
)
async def api_cases_approve(
    case_id: str,
    body: ApproveRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Record human approval; advance status to READY_TO_SEND."""
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No draft found for this case",
        )

    draft.approved_by = body.approved_by
    draft.approved_at = datetime.now(timezone.utc)
    case.status = CaseStatus.READY_TO_SEND
    await session.commit()
    broadcast_status(case_id, CaseStatus.READY_TO_SEND)
    return {"status": "approved", "case_id": case_id}


@router.get(
    "/cases/{case_id}/trace",
    operation_id="api_cases_trace",
    summary="Per-agent trace for timeline view",
)
async def api_cases_trace(
    case_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return all agent_runs for this case, ordered by latency for timeline view."""
    result = await session.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    runs_result = await session.execute(
        select(AgentRun)
        .where(AgentRun.case_id == case_id)
        .order_by(AgentRun.latency_ms)
    )
    runs = runs_result.scalars().all()

    return {
        "case_id": case_id,
        "agents": [
            {
                "agent": r.agent,
                "input_hash": r.input_hash,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "latency_ms": r.latency_ms,
                "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
                "trace_id": r.trace_id,
            }
            for r in runs
        ],
    }


@router.get(
    "/evals/latest",
    operation_id="api_evals_latest",
    summary="Latest eval run summary",
)
async def api_evals_latest() -> dict[str, Any]:
    """Return the most recently written eval results JSON from evals/results/."""
    if not _EVALS_RESULTS.exists():
        return {"error": "No eval results found", "results": []}

    result_files = sorted(
        _EVALS_RESULTS.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not result_files:
        return {"error": "No eval result files found", "results": []}

    latest = result_files[0]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        return {"file": latest.name, "results": data}
    except Exception as exc:
        return {"error": str(exc), "file": latest.name}


# ── Replay endpoints (§7.1, Day 7) ────────────────────────────────────────────

_REPLAY_DIR = _REPO_ROOT / "replay"


@router.get(
    "/replay/",
    operation_id="api_replay_list",
    summary="List available replay fixture case IDs",
)
async def api_replay_list() -> dict[str, list[str]]:
    """Return a list of case IDs that have recorded replay fixtures."""
    if not _REPLAY_DIR.exists():
        return {"cases": []}
    cases = [p.stem for p in sorted(_REPLAY_DIR.glob("*.json"))]
    return {"cases": cases}


@router.get(
    "/replay/{case_id}",
    operation_id="api_replay_get",
    summary="Serve a recorded replay fixture (§13 replay mode)",
)
async def api_replay_get(case_id: str) -> dict[str, Any]:
    """Return the pre-recorded fixture JSON for *case_id*.

    The fixture contains ``case``, ``trace``, ``summary``, ``draft``, and
    ``events`` keys that mirror the shapes of the live /api/* endpoints.
    Returns HTTP 404 if no fixture exists for this case.
    """
    fixture_path = _REPLAY_DIR / f"{case_id}.json"
    if not fixture_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Replay trace not found",
        )
    try:
        return json.loads(fixture_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read replay fixture %s: %s", fixture_path, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read replay fixture",
        ) from exc


# ── Embedded Orchestrate chat: short-lived JWT ────────────────────────────────


class ChatTokenResponse(BaseModel):
    security: bool
    token: str | None = None
    expires_in: int | None = None


@router.post(
    "/chat/token",
    operation_id="api_chat_token",
    response_model=ChatTokenResponse,
    summary="Short-lived JWT for the embedded watsonx Orchestrate web chat",
)
async def api_chat_token(request: Request) -> ChatTokenResponse:
    """Sign a JWT for the web chat with the backend-only private key.

    Returns ``{"security": false}`` when no key is configured (Orchestrate
    Embed Security off), so the page can load the chat without a token.
    """
    from services.chat_token import sign_chat_token

    key = f"chat-token:{_client_ip(request)}"
    if not check_rate_limit(key, limit=60, window_seconds=3600):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded — 60 chat tokens per hour per IP",
            headers={"Retry-After": str(retry_after_seconds(key))},
        )
    signed = sign_chat_token()
    if signed is None:
        return ChatTokenResponse(security=False)
    token, ttl = signed
    return ChatTokenResponse(security=True, token=token, expires_in=ttl)
