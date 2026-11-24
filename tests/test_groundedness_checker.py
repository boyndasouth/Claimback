"""Unit tests for Agent 11 — Groundedness Checker."""
from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import (
    AgentResult,
    AppealDraft,
    CaseContext,
    CheckerInput,
    GroundingReport,
    SourceRef,
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="c1", user_id="u1", trace_id="t1")


def _make_checker_input(cited: bool = True) -> CheckerInput:
    src = SourceRef(clause_id="c1", doc_id="d1", section="§4", text="medically necessary")
    citations = {"S1": src} if cited else {}
    draft = AppealDraft(
        body_markdown="The service is medically necessary [S1]." if cited else "The service is medically necessary.",
        citations=citations,
        version=1,
    )
    return CheckerInput(draft=draft, sources=[src])


class TestGroundednessChecker:
    def test_fully_cited_draft_passes(self) -> None:
        from agents.groundedness_checker import GroundednessChecker

        mock_response = json.dumps({
            "passed": True,
            "flagged_spans": [],
            "groundedness_score": 0.98,
        })
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=(mock_response, 80, 30))

        with patch("agents.groundedness_checker.get_client", return_value=mock_client):
            agent = GroundednessChecker()
            inp = _make_checker_input(cited=True)
            ctx = _make_ctx()

            import asyncio
            result: AgentResult[GroundingReport] = asyncio.run(agent.run(inp, ctx))

        assert result.output.passed is True
        assert len(result.output.flagged_spans) == 0
        assert result.output.groundedness_score == 0.98

    def test_uncited_sentence_fails(self) -> None:
        from agents.groundedness_checker import GroundednessChecker

        mock_response = json.dumps({
            "passed": False,
            "flagged_spans": [
                {"span": "The service is medically necessary.", "reason": "No citation provided."}
            ],
            "groundedness_score": 0.0,
        })
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=(mock_response, 80, 30))

        with patch("agents.groundedness_checker.get_client", return_value=mock_client):
            agent = GroundednessChecker()
            inp = _make_checker_input(cited=False)
            ctx = _make_ctx()

            import asyncio
            result: AgentResult[GroundingReport] = asyncio.run(agent.run(inp, ctx))

        assert result.output.passed is False
        assert len(result.output.flagged_spans) == 1
        assert result.output.flagged_spans[0].reason == "No citation provided."

    def test_guardian_fallback_to_routing(self) -> None:
        """When guardian tier fails, falls back to routing tier."""
        from agents.groundedness_checker import GroundednessChecker

        good_response = json.dumps({
            "passed": True,
            "flagged_spans": [],
            "groundedness_score": 0.96,
        })

        call_count = [0]

        async def _mock_generate(model_tier, prompt, **kw):
            call_count[0] += 1
            if model_tier == "guardian":
                raise RuntimeError("guardian unavailable")
            return good_response, 80, 30

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(side_effect=_mock_generate)

        with patch("agents.groundedness_checker.get_client", return_value=mock_client):
            agent = GroundednessChecker()
            inp = _make_checker_input(cited=True)
            ctx = _make_ctx()

            import asyncio
            result: AgentResult[GroundingReport] = asyncio.run(agent.run(inp, ctx))

        assert result.output.passed is True
        assert call_count[0] == 2  # guardian failed, routing succeeded
