"""Unit tests for Agent 13 — Follow-up Tracker."""
from __future__ import annotations

import pathlib
import sys
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import (
    AppealPlan,
    CaseContext,
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id=str(uuid.uuid4()), user_id="u1", trace_id="t1")


def _make_plan(external: bool) -> AppealPlan:
    base = date(2025, 8, 1)
    return AppealPlan(
        internal_appeal_deadline=base,
        external_review_available=external,
        external_review_deadline=(base + timedelta(days=60)) if external else None,
        expedited_eligible=False,
        rule_sources=[],
    )


class TestFollowupTracker:
    def test_internal_only_creates_one_reminder(self) -> None:
        """When external review is unavailable, only one reminder is created."""
        added = []

        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda x: added.append(x))
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("db.AsyncSessionFactory", mock_factory):
            from agents.followup_tracker import FollowupTracker
            agent = FollowupTracker()
            inp = _make_plan(external=False)
            ctx = _make_ctx()

            import asyncio
            result = asyncio.run(agent.run(inp, ctx))

        assert len(result.output) == 1
        assert result.output[0]["kind"] == "internal_appeal"

    def test_external_review_creates_two_or_three_reminders(self) -> None:
        """When external review is available, 2–3 reminders are created."""
        added = []

        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda x: added.append(x))
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("db.AsyncSessionFactory", mock_factory):
            from agents.followup_tracker import FollowupTracker
            agent = FollowupTracker()
            inp = _make_plan(external=True)
            ctx = _make_ctx()

            import asyncio
            result = asyncio.run(agent.run(inp, ctx))

        kinds = {r["kind"] for r in result.output}
        assert "internal_appeal" in kinds
        assert "external_review" in kinds
        assert len(result.output) >= 2
