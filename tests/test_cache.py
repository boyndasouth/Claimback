"""Unit tests for the idempotent cache helper (services/cache.py)."""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))


class TestGetCachedResult:
    def test_cache_hit_returns_dict(self) -> None:
        """When a matching agent_run exists, returns its output dict."""
        stored_output = {"output": {"probability": 0.72}, "confidence": 0.72}

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=stored_output)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("services.cache.AsyncSessionFactory", mock_factory):
            from services.cache import get_cached_result
            import asyncio
            result = asyncio.run(
                get_cached_result("case-1", "success_scorer", "abc123")
            )

        assert result == stored_output

    def test_cache_miss_returns_none(self) -> None:
        """When no matching run exists, returns None."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("services.cache.AsyncSessionFactory", mock_factory):
            from services.cache import get_cached_result
            import asyncio
            result = asyncio.run(
                get_cached_result("case-1", "success_scorer", "abc123")
            )

        assert result is None

    def test_db_error_returns_none_gracefully(self) -> None:
        """DB errors are swallowed and None is returned."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db down"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("services.cache.AsyncSessionFactory", mock_factory):
            from services.cache import get_cached_result
            import asyncio
            result = asyncio.run(
                get_cached_result("case-1", "appeal_writer", "xyz")
            )

        assert result is None


class TestHashInput:
    def test_deterministic(self) -> None:
        from services.cache import hash_input
        h1 = hash_input({"a": 1, "b": "hello"})
        h2 = hash_input({"a": 1, "b": "hello"})
        assert h1 == h2
        assert len(h1) == 16

    def test_different_inputs_different_hashes(self) -> None:
        from services.cache import hash_input
        h1 = hash_input({"a": 1})
        h2 = hash_input({"a": 2})
        assert h1 != h2
