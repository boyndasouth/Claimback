"""Tests for the tracing decorator (§11)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import AgentResult, CaseContext, SourceRef


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-123", user_id="user-1", trace_id="trace-abc")


def _make_result(tokens_in: int = 5, tokens_out: int = 10) -> AgentResult[str]:
    return AgentResult(
        output="hello",
        confidence=0.9,
        sources=[],
        trace_id="trace-abc",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=42,
    )


def _make_session_factory(session: MagicMock):
    """Return a callable that acts as async_sessionmaker returning *session* as context manager."""
    class _AsyncCtx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    def _factory():
        return _AsyncCtx()

    return _factory


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestTraceDecorator:
    async def test_decorator_returns_result(self):
        """Decorated method should return its value unchanged."""
        from services.tracing import trace

        class FakeAgent:
            name = "fake_agent"

            @trace(prompt_version="v1")
            async def run(self, input, ctx):  # noqa: A002
                return _make_result()

        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        with (
            patch("services.tracing.AsyncSessionFactory", _make_session_factory(mock_session)),
            patch("services.tracing._get_langfuse", return_value=None),
        ):
            agent = FakeAgent()
            result = await agent.run("some input", _make_ctx())

        assert isinstance(result, AgentResult)
        assert result.output == "hello"

    async def test_agent_run_persisted(self):
        """AgentRun row should be added to the session."""
        from services.tracing import trace

        class FakeAgent:
            name = "persist_test"

            @trace(prompt_version="v1")
            async def run(self, input, ctx):  # noqa: A002
                return _make_result()

        added_objects: list = []

        mock_session = MagicMock()
        mock_session.add = lambda obj: added_objects.append(obj)
        mock_session.commit = AsyncMock()

        with (
            patch("services.tracing.AsyncSessionFactory", _make_session_factory(mock_session)),
            patch("services.tracing._get_langfuse", return_value=None),
        ):
            agent = FakeAgent()
            await agent.run("inp", _make_ctx())

        from db.models import AgentRun

        assert any(isinstance(o, AgentRun) for o in added_objects), (
            "No AgentRun added to session"
        )
        run = next(o for o in added_objects if isinstance(o, AgentRun))
        assert run.agent == "persist_test"
        assert run.case_id == "case-123"

    async def test_exception_is_reraised(self):
        """Exceptions from the wrapped function must propagate."""
        from services.tracing import trace

        class BrokenAgent:
            name = "broken"

            @trace(prompt_version="v1")
            async def run(self, input, ctx):  # noqa: A002
                raise ValueError("boom")

        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        with (
            patch("services.tracing.AsyncSessionFactory", _make_session_factory(mock_session)),
            patch("services.tracing._get_langfuse", return_value=None),
        ):
            with pytest.raises(ValueError, match="boom"):
                await BrokenAgent().run("x", _make_ctx())

    async def test_langfuse_skipped_when_no_creds(self, monkeypatch):
        """If LANGFUSE_PUBLIC_KEY is absent, no Langfuse client is created."""
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        # Reset the cached singleton
        import services.tracing as tr
        tr._langfuse = None
        tr._langfuse_checked = False

        # Reset checked flag so it re-evaluates env
        tr._langfuse_checked = False
        lf = tr._get_langfuse()

        assert lf is None
