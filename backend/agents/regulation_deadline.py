"""Agent 8 — Regulation & Deadline.

Fully deterministic date math — no LLM call.  Looks up the applicable state
appeal rules from ``data/fixtures/state_rules.json`` and computes concrete
deadline dates from ``ExtractedClaim.denial_date``.

Input:  DeadlineInput  (ExtractedClaim + DenialCategory)
Output: AppealPlan

Deadline calculation notes
--------------------------
- Internal deadline  = denial_date + internal_days calendar days.
- External deadline  = denial_date + (internal_days + external_days) calendar
  days (only when external review is available for the state).
- Expedited eligible = denial is MEDICAL_NECESSITY **and** billed_amount >= $500.
- If the claim's state is not in the fixture, federal ERISA defaults apply:
  180 days internal, 60 days external review, external available, no expedited.
- Deadlines are **calendar** days — no business-day adjustment.  State rules
  specify calendar days per regulatory convention; implementers should not
  convert to business days unless a specific state rule requires it.
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel

from app.models import (
    AgentResult,
    AppealPlan,
    CaseContext,
    DenialCategory,
    ExtractedClaim,
    SourceRef,
)
from services.tracing import trace

logger = logging.getLogger(__name__)

# ── Fixture loading (once at import time) ─────────────────────────────────────

_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "data" / "fixtures" / "state_rules.json"
)

_FIXTURE_DOC_ID = "state_rules_fixture"

# Federal ERISA fallback when state is not in fixture
_ERISA_DEFAULTS: dict[str, object] = {
    "state": "US",
    "state_name": "Federal ERISA Default",
    "internal_days": 180,
    "external_review_available": True,
    "external_days": 60,
    "expedited_days": 3,
}

# Expedited threshold (billed amount must be >= this for expedited eligibility)
_EXPEDITED_AMOUNT_THRESHOLD = Decimal("500.00")


def _load_state_rules() -> dict[str, dict[str, object]]:
    """Return a dict keyed by 2-letter state code."""
    rows: list[dict[str, object]] = json.loads(_FIXTURE_PATH.read_text())
    return {str(r["state"]): r for r in rows}


_STATE_RULES: dict[str, dict[str, object]] = _load_state_rules()


# ── Input model ───────────────────────────────────────────────────────────────


class DeadlineInput(BaseModel):
    """Combined input for Agent 8 — claim fields + denial category from Agent 5."""

    claim: ExtractedClaim
    denial_category: DenialCategory


# ── Date math helpers ─────────────────────────────────────────────────────────


def _compute_deadlines(
    denial_date: date,
    rule: dict[str, object],
    denial_category: DenialCategory,
    billed_amount: Decimal,
) -> tuple[date, bool, date | None, bool]:
    """Return (internal_deadline, external_available, external_deadline, expedited).

    All deadlines are calendar days from denial_date.
    """
    internal_days = int(str(rule["internal_days"]))
    external_available = bool(rule["external_review_available"])
    external_days = int(str(rule["external_days"])) if rule.get("external_days") else 0

    internal_deadline = denial_date + timedelta(days=internal_days)

    external_deadline: date | None = None
    if external_available and external_days:
        external_deadline = denial_date + timedelta(days=internal_days + external_days)

    expedited_eligible = (
        denial_category == DenialCategory.MEDICAL_NECESSITY
        and billed_amount >= _EXPEDITED_AMOUNT_THRESHOLD
    )

    return internal_deadline, external_available, external_deadline, expedited_eligible


# ── Agent ─────────────────────────────────────────────────────────────────────


class RegulationDeadline:
    """Agent 8 — deterministic appeal-deadline calculation."""

    name = "regulation_deadline"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: DeadlineInput,
        ctx: CaseContext,
    ) -> AgentResult[AppealPlan]:
        claim = input.claim

        # Require denial_date — raise clearly if missing (should never happen
        # after Agent 3, but guard defensively)
        if claim.denial_date is None:
            raise ValueError(
                f"Case {ctx.case_id}: denial_date is required to compute appeal deadlines."
            )

        rule = _STATE_RULES.get(claim.state, _ERISA_DEFAULTS)
        state_name = str(rule.get("state_name", "Federal ERISA Default"))
        state_code = str(rule.get("state", claim.state))

        internal_deadline, external_available, external_deadline, expedited = (
            _compute_deadlines(
                denial_date=claim.denial_date,
                rule=rule,
                denial_category=input.denial_category,
                billed_amount=claim.billed_amount,
            )
        )

        rule_source = SourceRef(
            clause_id=state_code,
            doc_id=_FIXTURE_DOC_ID,
            section=state_name,
            text=(
                f"Internal appeal deadline: {rule['internal_days']} days. "
                + (
                    f"External review: {rule['external_days']} days."
                    if external_available
                    else "External review: not available."
                )
            ),
        )

        appeal_plan = AppealPlan(
            internal_appeal_deadline=internal_deadline,
            external_review_available=external_available,
            external_review_deadline=external_deadline,
            expedited_eligible=expedited,
            rule_sources=[rule_source],
        )

        return AgentResult(
            output=appeal_plan,
            confidence=1.0,
            sources=[rule_source],
            trace_id=ctx.trace_id,
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,  # filled in by @trace
        )
