"""Tests for Agent 6 — Policy Retriever."""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import CaseContext, ExtractedClaim, PolicyClause


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-006", user_id="u-6", trace_id="t-6")


def _claim() -> ExtractedClaim:
    return ExtractedClaim(
        claim_id="CLM-006",
        payer="UnitedHealth",
        plan_type="employer",
        state="CA",
        service_date="2024-03-01",
        denial_date="2024-03-15",
        cpt_codes=["70553"],
        icd10_codes=["G35"],
        billed_amount=Decimal("2500.00"),
        denial_reason_text="Service not medically necessary per utilization review.",
        denial_reason_code="CO-50",
        field_confidence={},
    )


def _make_fake_rows(n: int = 3) -> list[tuple[str, str, str, dict, float]]:
    """Create *n* fake DB result rows in (id, doc_id, text, metadata, rrf_score) form."""
    return [
        (
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            f"Policy clause text {i}: coverage applies when medically necessary.",
            {"section": f"Section {i}.1", "content_hash": f"hash{i}"},
            0.9 - i * 0.1,
        )
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _patch_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    class _AsyncCtx:
        async def __aenter__(self) -> MagicMock:
            return mock_session

        async def __aexit__(self, *a: object) -> bool:
            return False

    monkeypatch.setattr("services.tracing.AsyncSessionFactory", lambda: _AsyncCtx())
    monkeypatch.setattr("services.tracing._get_langfuse", lambda: None)


class TestPolicyRetrieverOutputShape:
    async def test_returns_list_of_policy_clauses(self) -> None:
        """Agent must return AgentResult whose output is a list[PolicyClause]."""
        from agents.policy_retriever import PolicyRetriever

        fake_rows = _make_fake_rows(3)
        fake_vector = [0.1] * 768

        # Mock embed call
        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[fake_vector])

        # Mock DB session execute
        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = fake_rows

        mock_db_session = MagicMock()
        mock_db_session.execute = AsyncMock(return_value=mock_execute_result)

        class _FakeAsyncCtx:
            async def __aenter__(self) -> MagicMock:
                return mock_db_session

            async def __aexit__(self, *a: object) -> bool:
                return False

        agent = PolicyRetriever()

        with (
            patch("agents.policy_retriever.get_client", return_value=mock_client),
            patch("agents.policy_retriever.AsyncSessionFactory", return_value=_FakeAsyncCtx()),
        ):
            result = await agent.run(_claim(), _make_ctx())

        assert isinstance(result.output, list)
        assert len(result.output) == 3
        for clause in result.output:
            assert isinstance(clause, PolicyClause)

    async def test_policy_clause_fields_populated(self) -> None:
        """Every PolicyClause must have non-empty clause_id, doc_id, section, text."""
        from agents.policy_retriever import PolicyRetriever

        fake_rows = _make_fake_rows(2)
        fake_vector = [0.0] * 768

        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[fake_vector])

        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = fake_rows

        mock_db_session = MagicMock()
        mock_db_session.execute = AsyncMock(return_value=mock_execute_result)

        class _FakeAsyncCtx:
            async def __aenter__(self) -> MagicMock:
                return mock_db_session

            async def __aexit__(self, *a: object) -> bool:
                return False

        agent = PolicyRetriever()

        with (
            patch("agents.policy_retriever.get_client", return_value=mock_client),
            patch("agents.policy_retriever.AsyncSessionFactory", return_value=_FakeAsyncCtx()),
        ):
            result = await agent.run(_claim(), _make_ctx())

        for clause in result.output:
            assert clause.clause_id
            assert clause.doc_id
            assert clause.section
            assert clause.text
            assert isinstance(clause.score, float)

    async def test_empty_db_result_returns_empty_list(self) -> None:
        """Zero DB rows → empty list output, confidence = 0.0."""
        from agents.policy_retriever import PolicyRetriever

        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[[0.0] * 768])

        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = []

        mock_db_session = MagicMock()
        mock_db_session.execute = AsyncMock(return_value=mock_execute_result)

        class _FakeAsyncCtx:
            async def __aenter__(self) -> MagicMock:
                return mock_db_session

            async def __aexit__(self, *a: object) -> bool:
                return False

        agent = PolicyRetriever()

        with (
            patch("agents.policy_retriever.get_client", return_value=mock_client),
            patch("agents.policy_retriever.AsyncSessionFactory", return_value=_FakeAsyncCtx()),
        ):
            result = await agent.run(_claim(), _make_ctx())

        assert result.output == []
        assert result.confidence == 0.0


class TestPolicyRetrieverEmbedCall:
    async def test_embed_called_with_query_string(self) -> None:
        """embed() must be called once with a list containing one query string."""
        from agents.policy_retriever import PolicyRetriever

        mock_client = MagicMock()
        embed_calls: list[list[str]] = []

        async def _capture_embed(texts: list[str]) -> list[list[float]]:
            embed_calls.append(texts)
            return [[0.1] * 768]

        mock_client.embed = _capture_embed

        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = []

        mock_db_session = MagicMock()
        mock_db_session.execute = AsyncMock(return_value=mock_execute_result)

        class _FakeAsyncCtx:
            async def __aenter__(self) -> MagicMock:
                return mock_db_session

            async def __aexit__(self, *a: object) -> bool:
                return False

        agent = PolicyRetriever()

        with (
            patch("agents.policy_retriever.get_client", return_value=mock_client),
            patch("agents.policy_retriever.AsyncSessionFactory", return_value=_FakeAsyncCtx()),
        ):
            await agent.run(_claim(), _make_ctx())

        assert len(embed_calls) == 1
        assert isinstance(embed_calls[0], list)
        assert len(embed_calls[0]) == 1
        # Query string should include denial_reason_text and CPT/ICD
        query = embed_calls[0][0]
        assert "medically necessary" in query.lower()
        assert "70553" in query
        assert "G35" in query

    async def test_tokens_are_zero(self) -> None:
        """Embedding calls do not expose token counts — both must be 0."""
        from agents.policy_retriever import PolicyRetriever

        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[[0.0] * 768])

        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = _make_fake_rows(1)

        mock_db_session = MagicMock()
        mock_db_session.execute = AsyncMock(return_value=mock_execute_result)

        class _FakeAsyncCtx:
            async def __aenter__(self) -> MagicMock:
                return mock_db_session

            async def __aexit__(self, *a: object) -> bool:
                return False

        agent = PolicyRetriever()

        with (
            patch("agents.policy_retriever.get_client", return_value=mock_client),
            patch("agents.policy_retriever.AsyncSessionFactory", return_value=_FakeAsyncCtx()),
        ):
            result = await agent.run(_claim(), _make_ctx())

        assert result.tokens_in == 0
        assert result.tokens_out == 0


class TestPolicyRetrieverSources:
    async def test_sources_match_clauses(self) -> None:
        """AgentResult.sources must contain one SourceRef per returned PolicyClause."""
        from agents.policy_retriever import PolicyRetriever

        fake_rows = _make_fake_rows(5)
        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[[0.0] * 768])

        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = fake_rows

        mock_db_session = MagicMock()
        mock_db_session.execute = AsyncMock(return_value=mock_execute_result)

        class _FakeAsyncCtx:
            async def __aenter__(self) -> MagicMock:
                return mock_db_session

            async def __aexit__(self, *a: object) -> bool:
                return False

        agent = PolicyRetriever()

        with (
            patch("agents.policy_retriever.get_client", return_value=mock_client),
            patch("agents.policy_retriever.AsyncSessionFactory", return_value=_FakeAsyncCtx()),
        ):
            result = await agent.run(_claim(), _make_ctx())

        assert len(result.sources) == len(result.output)
        clause_ids = {c.clause_id for c in result.output}
        source_ids = {s.clause_id for s in result.sources}
        assert clause_ids == source_ids
