"""Agent 13 — Follow-up Tracker (§5).

Reads deadlines from the :class:`~app.models.AppealPlan` and schedules
:class:`~db.models.Reminder` rows in Postgres for the internal appeal deadline,
the external review deadline (if available), and an escalation reminder
(half-way to the external deadline).

No LLM call is made — this agent is purely orchestration logic.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from app.models import AgentResult, AppealPlan, CaseContext, SourceRef
from services.tracing import trace

logger = logging.getLogger(__name__)

_KIND_INTERNAL = "internal_appeal"
_KIND_EXTERNAL = "external_review"
_KIND_ESCALATION = "escalation"


class FollowupTracker:
    """Agent 13 — Follow-up Tracker.

    Schedules :class:`~db.models.Reminder` rows.  Returns a list of the
    created reminder dicts (not ORM objects) so the result can be JSON-serialised
    by the ``@trace`` decorator without requiring a live DB session.
    """

    name = "followup_tracker"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: AppealPlan,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[list[dict]]:
        """Schedule reminders from *input* (AppealPlan).

        Parameters
        ----------
        input:
            The plan from Agent 8.
        ctx:
            Ambient case context (provides case_id).
        """
        from db import AsyncSessionFactory
        from db.models import Reminder

        start = time.monotonic()
        created: list[dict] = []

        async with AsyncSessionFactory() as session:
            # Internal appeal deadline (always present)
            due_at_internal = datetime.combine(
                input.internal_appeal_deadline, datetime.min.time()
            ).replace(tzinfo=timezone.utc)
            r1 = Reminder(
                id=str(uuid.uuid4()),
                case_id=ctx.case_id,
                due_at=due_at_internal,
                kind=_KIND_INTERNAL,
                sent=False,
            )
            session.add(r1)
            created.append({"kind": _KIND_INTERNAL, "due_at": str(due_at_internal)})

            # External review deadline
            if input.external_review_available and input.external_review_deadline:
                due_at_ext = datetime.combine(
                    input.external_review_deadline, datetime.min.time()
                ).replace(tzinfo=timezone.utc)
                r2 = Reminder(
                    id=str(uuid.uuid4()),
                    case_id=ctx.case_id,
                    due_at=due_at_ext,
                    kind=_KIND_EXTERNAL,
                    sent=False,
                )
                session.add(r2)
                created.append({"kind": _KIND_EXTERNAL, "due_at": str(due_at_ext)})

                # Escalation halfway between internal and external deadlines
                mid_days = (
                    input.external_review_deadline - input.internal_appeal_deadline
                ).days // 2
                if mid_days > 0:
                    from datetime import timedelta

                    due_at_esc = due_at_internal + timedelta(days=mid_days)
                    r3 = Reminder(
                        id=str(uuid.uuid4()),
                        case_id=ctx.case_id,
                        due_at=due_at_esc,
                        kind=_KIND_ESCALATION,
                        sent=False,
                    )
                    session.add(r3)
                    created.append(
                        {"kind": _KIND_ESCALATION, "due_at": str(due_at_esc)}
                    )

            await session.commit()

        latency_ms = int((time.monotonic() - start) * 1000)

        return AgentResult[list[dict]](
            output=created,
            confidence=1.0,
            sources=[],
            trace_id=ctx.trace_id,
            tokens_in=0,
            tokens_out=0,
            latency_ms=latency_ms,
        )
