"""Shared case-summary service (§7.1 GET /api/cases/{id}/summary).

Both the frontend API and the Orchestrate ``case_get_explanation`` tool use
``get_case_summary`` so the two entry points never disagree.

Fields are null until the producing agent has written its result to
``agent_runs``.  Validation errors on individual agents leave that field null
but do not break the others.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppealPlan,
    CodeIssue,
    SuccessEstimate,
)
from app.models.appeal import EvidenceChecklist
from db.models import AgentRun, Case

logger = logging.getLogger(__name__)

# Maps the agent.name stored in agent_runs to the §6 model and the field in CaseSummary.
# Agents record themselves under their snake_case ``name`` (e.g.
# "regulation_deadline"); older rows/tests may use CamelCase, so both are
# accepted and normalised with _canonical().
_AGENT_FIELD_MAP: dict[str, str] = {
    "denial_classifier": "explanation",
    "regulation_deadline": "appeal_plan",
    "evidence_builder": "evidence_checklist",
    "code_validator": "code_issues",
    "success_scorer": "success_estimate",
}


def _canonical(agent: str) -> str:
    """'RegulationDeadline' / 'regulation_deadline' -> 'regulation_deadline'."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", agent).lower()


_ALL_AGENT_NAMES = list(_AGENT_FIELD_MAP) + [
    "".join(w.title() for w in k.split("_")) for k in _AGENT_FIELD_MAP
]


class CaseSummary(BaseModel):
    """Typed summary of a case assembled from agent_runs output JSONB."""

    explanation: str | None = None
    appeal_plan: AppealPlan | None = None
    evidence_checklist: EvidenceChecklist | None = None
    code_issues: list[CodeIssue] = []
    success_estimate: SuccessEstimate | None = None


def _parse_agent_output(agent: str, output: dict[str, Any] | str | None) -> Any:
    """Parse an agent_runs.output JSONB blob into the matching §6 model.

    ``output`` is a dict on Postgres (JSONB) and may be a JSON string on SQLite
    (used in tests).  Returns None on validation error so callers can treat
    missing/bad rows as "not yet available" rather than raising.
    """
    if isinstance(output, str):
        import json as _json
        try:
            output = _json.loads(output)
        except (ValueError, TypeError):
            return None
    if not isinstance(output, dict):
        return None
    agent = _canonical(agent)
    try:
        if agent == "denial_classifier":
            # Stored as {"output": [category, rationale], ...} (tuple → list);
            # older shape: {"category": "...", "rationale": "..."}
            inner = output.get("output", output)
            if isinstance(inner, list) and len(inner) >= 2:
                return str(inner[1]) or None
            if isinstance(inner, dict):
                return inner.get("rationale") or inner.get("category") or None
            return None

        if agent == "regulation_deadline":
            inner = output.get("output", output)
            return AppealPlan.model_validate(inner)

        if agent == "evidence_builder":
            inner = output.get("output", output)
            return EvidenceChecklist.model_validate(inner)

        if agent == "code_validator":
            inner = output.get("output", output)
            if isinstance(inner, list):
                return [CodeIssue.model_validate(item) for item in inner]
            return []

        if agent == "success_scorer":
            inner = output.get("output", output)
            return SuccessEstimate.model_validate(inner)

    except (ValidationError, TypeError, KeyError) as exc:
        logger.debug("Could not parse agent_run output for %s: %s", agent, exc)

    return None


async def get_case_summary(
    case_id: str,
    session: AsyncSession,
) -> CaseSummary | None:
    """Return a CaseSummary assembled from agent_runs for *case_id*.

    Returns None if the case does not exist.
    Returns a CaseSummary with all-null fields if no relevant agents have run.
    """
    # Verify the case exists
    case_result = await session.execute(select(Case).where(Case.id == case_id))
    if case_result.scalar_one_or_none() is None:
        return None

    runs_result = await session.execute(
        select(AgentRun)
        .where(AgentRun.case_id == case_id)
        .where(AgentRun.agent.in_(_ALL_AGENT_NAMES))
        .order_by(AgentRun.latency_ms)
    )
    runs = runs_result.scalars().all()

    summary = CaseSummary()

    for run in runs:
        parsed = _parse_agent_output(run.agent, run.output or {})
        if parsed is None:
            continue

        agent = _canonical(run.agent)
        if agent == "denial_classifier" and isinstance(parsed, str):
            summary.explanation = parsed
        elif agent == "regulation_deadline" and isinstance(parsed, AppealPlan):
            summary.appeal_plan = parsed
        elif agent == "evidence_builder" and isinstance(parsed, EvidenceChecklist):
            summary.evidence_checklist = parsed
        elif agent == "code_validator" and isinstance(parsed, list):
            summary.code_issues = parsed
        elif agent == "success_scorer" and isinstance(parsed, SuccessEstimate):
            summary.success_estimate = parsed

    return summary
