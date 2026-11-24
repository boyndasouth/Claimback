"""Unit tests for Agent 10 — Appeal Writer."""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import (
    AgentResult,
    AppealDraft,
    AppealPlan,
    CaseContext,
    CodeIssue,
    DenialCategory,
    EvidenceChecklist,
    EvidenceItem,
    ExtractedClaim,
    FlaggedSpan,
    PolicyClause,
    SourceRef,
    WriterInput,
    WriterRetryInput,
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="c1", user_id="u1", trace_id="t1")


def _make_writer_input() -> WriterInput:
    src = SourceRef(clause_id="c1", doc_id="d1", section="§4", text="medically necessary")
    clause = PolicyClause(clause_id="c1", doc_id="d1", section="§4", text="medically necessary", score=0.9)
    claim = ExtractedClaim(
        claim_id="CLM-001",
        payer="Acme",
        plan_type="employer",
        state="CA",
        service_date=date(2025, 1, 1),
        denial_date=date(2025, 2, 1),
        cpt_codes=["99213"],
        icd10_codes=["Z00.00"],
        billed_amount=Decimal("450.00"),
        denial_reason_text="Not medically necessary",
        denial_reason_code="CO-50",
        field_confidence={},
    )
    plan = AppealPlan(
        internal_appeal_deadline=date(2025, 8, 1),
        external_review_available=False,
        external_review_deadline=None,
        expedited_eligible=False,
        rule_sources=[src],
    )
    checklist = EvidenceChecklist(items=[
        EvidenceItem(item_id="E1", description="LON", rationale="rebuts denial", priority="high")
    ])
    return WriterInput(checklist=checklist, claim=claim, clauses=[clause], issues=[], plan=plan)


def _make_draft_response(version: int = 1) -> str:
    return json.dumps({
        "body_markdown": "Appeal letter. Claim supported [S1]. Policy cited [S1].",
        "citations": {
            "S1": {
                "clause_id": "c1",
                "doc_id": "d1",
                "section": "§4",
                "text": "medically necessary",
            }
        },
    })


class TestAppealWriter:
    def test_first_draft_version_is_one(self) -> None:
        from agents.appeal_writer import AppealWriter

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=(_make_draft_response(), 200, 100))

        with patch("agents.appeal_writer.get_client", return_value=mock_client):
            agent = AppealWriter()
            inp = _make_writer_input()
            ctx = _make_ctx()

            import asyncio
            result: AgentResult[AppealDraft] = asyncio.run(agent.run(inp, ctx))

        assert result.output.version == 1
        assert "S1" in result.output.citations
        # All [S#] tags in body must have citations
        tags = set(re.findall(r"S\d+", result.output.body_markdown))
        assert tags <= set(result.output.citations.keys())

    def test_retry_input_increments_version(self) -> None:
        from agents.appeal_writer import AppealWriter

        previous_draft = AppealDraft(
            body_markdown="old draft [S1]",
            citations={"S1": SourceRef(clause_id="c1", doc_id="d1", section="§4", text="x")},
            version=1,
        )
        base = _make_writer_input()
        retry_inp = WriterRetryInput(
            base=base,
            flagged_spans=[FlaggedSpan(span="old claim", reason="unsupported")],
            previous_draft=previous_draft,
        )

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=(_make_draft_response(), 200, 100))

        with patch("agents.appeal_writer.get_client", return_value=mock_client):
            agent = AppealWriter()
            ctx = _make_ctx()

            import asyncio
            result: AgentResult[AppealDraft] = asyncio.run(agent.run(retry_inp, ctx))

        assert result.output.version == 2  # previous_draft.version + 1

    def test_parse_error_raises(self) -> None:
        from agents.appeal_writer import AppealWriter

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=("bad json", 10, 5))

        with patch("agents.appeal_writer.get_client", return_value=mock_client):
            agent = AppealWriter()
            inp = _make_writer_input()
            ctx = _make_ctx()

            import asyncio
            with pytest.raises(ValueError, match="AppealWriter"):
                asyncio.run(agent.run(inp, ctx))


# ── Citations are built from retrieved clauses, never from the model ─────────


async def test_citations_come_from_clauses_and_nested_fences_parse():
    from agents.appeal_writer import AppealWriter

    # Model output: outer ```json fence, an inner fenced block inside the body,
    # an invented "S9" key, and a fabricated citations object to be ignored.
    body = (
        "I am appealing claim CLM-001. The policy covers necessary care [S1]. "
        "Also [S9].\n\n```json\n{\\\"x\\\": 1}\n```"
    )
    raw = (
        "```json\n{\"body_markdown\": \"" + body + "\", "
        "\"citations\": {\"S1\": {\"clause_id\": \"fake\", \"doc_id\": \"fake\", "
        "\"section\": \"fake\", \"text\": \"fake\"}}}\n```"
    )
    client = MagicMock()
    client.generate = AsyncMock(return_value=(raw, 10, 20))
    with patch("agents.appeal_writer.get_client", return_value=client):
        result = await AppealWriter().run.__wrapped__(AppealWriter(), _make_writer_input(), _make_ctx())

    draft = result.output
    assert set(draft.citations) == {"S1"}            # S9 dropped
    assert draft.citations["S1"].clause_id == "c1"   # real clause, not "fake"
    assert "```json" in draft.body_markdown          # inner fence kept intact


def test_extract_json_block_handles_inner_fence():
    from services.json_utils import extract_json_block

    raw = '```json\n{"body_markdown": "a\n```json\n{}\n```", "k": 1}\n```'
    assert json.loads(extract_json_block(raw))["k"] == 1


async def test_retries_once_when_output_is_truncated():
    from agents.appeal_writer import AppealWriter

    truncated = '{"body_markdown": "I am appealing [S1].\n> \n> \n> \n> '
    good = json.dumps({"body_markdown": "I am appealing claim CLM-001 [S1]."})
    client = MagicMock()
    client.generate = AsyncMock(side_effect=[(truncated, 10, 2500), (good, 10, 30)])
    with patch("agents.appeal_writer.get_client", return_value=client):
        result = await AppealWriter().run.__wrapped__(AppealWriter(), _make_writer_input(), _make_ctx())

    assert client.generate.await_count == 2
    assert result.output.body_markdown == "I am appealing claim CLM-001 [S1]."
    assert result.tokens_out == 2530


def test_clean_body_collapses_empty_quote_runs():
    from agents.appeal_writer import _clean_body

    body = "> quote line\n> \n> \n> \n> \n\n\n\nNext paragraph"
    assert _clean_body(body) == "> quote line\n>\n\nNext paragraph"
