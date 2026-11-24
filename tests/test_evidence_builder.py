"""Unit tests for Agent 9 — Evidence Builder."""
from __future__ import annotations

import json
import pathlib
import sys
from decimal import Decimal
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import (
    AgentResult,
    CaseContext,
    CodeIssue,
    DenialCategory,
    EvidenceBuilderInput,
    EvidenceChecklist,
    PolicyClause,
    SourceRef,
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="c1", user_id="u1", trace_id="t1")


def _make_input() -> EvidenceBuilderInput:
    src = SourceRef(clause_id="c1", doc_id="d1", section="§4", text="medically necessary")
    clause = PolicyClause(clause_id="c1", doc_id="d1", section="§4", text="medically necessary", score=0.9)
    return EvidenceBuilderInput(
        category=DenialCategory.MEDICAL_NECESSITY,
        clauses=[clause],
        issues=[],
    )


class TestEvidenceBuilder:
    def test_happy_path(self) -> None:
        """Mock watsonx; assert AgentResult with non-empty checklist returned."""
        from agents.evidence_builder import EvidenceBuilder

        mock_response = json.dumps({
            "items": [
                {
                    "item_id": "E1",
                    "description": "Letter of medical necessity",
                    "rationale": "Directly rebuts denial.",
                    "priority": "high",
                }
            ]
        })

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=(mock_response, 100, 50))

        with patch("agents.evidence_builder.get_client", return_value=mock_client):
            agent = EvidenceBuilder()
            inp = _make_input()
            ctx = _make_ctx()

            import asyncio
            result: AgentResult[EvidenceChecklist] = asyncio.run(agent.run(inp, ctx))

        assert isinstance(result, AgentResult)
        assert isinstance(result.output, EvidenceChecklist)
        assert len(result.output.items) == 1
        assert result.output.items[0].item_id == "E1"
        assert result.output.items[0].priority == "high"
        assert result.tokens_in == 100
        assert result.tokens_out == 50

    def test_parse_error_raises_value_error(self) -> None:
        """When model returns garbage, ValueError is raised."""
        from agents.evidence_builder import EvidenceBuilder

        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=("not json at all", 10, 5))

        with patch("agents.evidence_builder.get_client", return_value=mock_client):
            agent = EvidenceBuilder()
            inp = _make_input()
            ctx = _make_ctx()

            import asyncio
            with pytest.raises(ValueError, match="EvidenceBuilder"):
                asyncio.run(agent.run(inp, ctx))
