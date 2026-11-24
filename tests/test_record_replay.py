"""Unit tests for scripts/record_replay.py (Day 7)."""
from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the backend package is importable when running from repo root.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BACKEND = str(_REPO_ROOT / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ---------------------------------------------------------------------------
# Lightweight stubs so we can import the script without a live DB.
# ---------------------------------------------------------------------------


def _make_case(case_id: str, status: str = "AWAITING_APPROVAL") -> MagicMock:
    c = MagicMock()
    c.id = case_id
    c.status = status
    c.category = "medical_necessity"
    c.success_prob = 0.72
    return c


def _make_run(agent: str, case_id: str) -> MagicMock:
    r = MagicMock()
    r.agent = agent
    r.case_id = case_id
    r.input_hash = "abc123"
    r.tokens_in = 100
    r.tokens_out = 50
    r.latency_ms = 300
    r.cost_usd = 0.000025
    r.trace_id = "trace-001"
    return r


def _make_draft(case_id: str) -> MagicMock:
    d = MagicMock()
    d.case_id = case_id
    d.version = 1
    d.body = "## Appeal\n\nWe appeal this denial..."
    d.grounding_report = {"passed": True, "flagged_spans": [], "groundedness_score": 0.97}
    d.approved_by = None
    d.approved_at = None
    return d


# ---------------------------------------------------------------------------
# Actual tests
# ---------------------------------------------------------------------------


class _FakeSessionContext:
    """Context manager that returns a session mock."""

    def __init__(self, session: MagicMock) -> None:
        self._session = session

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *_: object) -> None:
        pass


@pytest.mark.asyncio
async def test_record_case_writes_fixture(tmp_path: pathlib.Path) -> None:
    """_record_case should write a valid fixture JSON with the required keys."""
    case_id = "test-case-0001"

    # Build mock session
    session = MagicMock()

    # Case query
    case_obj = _make_case(case_id)
    case_execute_result = MagicMock()
    case_execute_result.scalar_one_or_none.return_value = case_obj

    # AgentRun query
    runs = [_make_run("DocumentExtractor", case_id), _make_run("DenialClassifier", case_id)]
    runs_execute_result = MagicMock()
    runs_execute_result.scalars.return_value.all.return_value = runs

    # Draft query
    draft_obj = _make_draft(case_id)
    draft_execute_result = MagicMock()
    draft_execute_result.scalar_one_or_none.return_value = draft_obj

    # Wire up execute to return different results per call
    session.execute = AsyncMock(
        side_effect=[case_execute_result, runs_execute_result, draft_execute_result]
    )

    # Fake summary
    from services.case_summary import CaseSummary
    fake_summary = CaseSummary(explanation="Claim denied for medical necessity.")

    with (
        patch("scripts.record_replay.AsyncSessionFactory", return_value=_FakeSessionContext(session)),
        patch("scripts.record_replay.get_case_summary", AsyncMock(return_value=fake_summary)),
        patch("scripts.record_replay._REPLAY_DIR", tmp_path),
    ):
        from scripts.record_replay import _record_case
        await _record_case(case_id)

    out_file = tmp_path / f"{case_id}.json"
    assert out_file.exists(), "Fixture file was not written"

    data = json.loads(out_file.read_text(encoding="utf-8"))

    # Top-level keys must all be present
    assert set(data.keys()) == {"case", "trace", "summary", "draft", "events"}

    # case sub-object
    assert data["case"]["case_id"] == case_id
    assert data["case"]["status"] == "AWAITING_APPROVAL"
    assert data["case"]["category"] == "medical_necessity"

    # trace sub-object
    assert data["trace"]["case_id"] == case_id
    assert len(data["trace"]["agents"]) == 2
    assert data["trace"]["agents"][0]["agent"] == "DocumentExtractor"

    # draft sub-object
    assert data["draft"]["version"] == 1
    assert "Appeal" in data["draft"]["body_markdown"]

    # summary sub-object
    assert data["summary"]["explanation"] == "Claim denied for medical necessity."

    # events array must be non-empty and have elapsed_ms + type
    assert len(data["events"]) > 0
    for ev in data["events"]:
        assert "elapsed_ms" in ev
        assert "type" in ev


@pytest.mark.asyncio
async def test_record_case_missing_case_skips(tmp_path: pathlib.Path) -> None:
    """_record_case should log and return cleanly when the case does not exist."""
    session = MagicMock()
    missing_result = MagicMock()
    missing_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=missing_result)

    with (
        patch("scripts.record_replay.AsyncSessionFactory", return_value=_FakeSessionContext(session)),
        patch("scripts.record_replay._REPLAY_DIR", tmp_path),
    ):
        from scripts.record_replay import _record_case
        await _record_case("nonexistent-case")

    # No file should be written
    assert list(tmp_path.glob("*.json")) == []


def test_make_events_contains_terminal_event() -> None:
    """_make_events should end with the terminal done event."""
    from scripts.record_replay import _make_events

    events = _make_events("AWAITING_APPROVAL", 0.72)
    assert events[-1]["type"] == "done"
    assert events[-1]["status"] == "AWAITING_APPROVAL"
    assert events[-1]["success_prob"] == pytest.approx(0.72)


def test_make_events_needs_review() -> None:
    from scripts.record_replay import _make_events

    events = _make_events("NEEDS_HUMAN_REVIEW", None)
    assert events[-1]["type"] == "needs_review"
