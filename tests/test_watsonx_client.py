"""Tests for WatsonxClient — timeout, retry, and circuit breaker (§16)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

# Reset the module-level singleton and circuit breaker state between tests


@pytest.fixture(autouse=True)
def reset_watsonx_state():
    """Reset module globals between tests."""
    import services.watsonx as wx

    wx._client = None
    wx._circuit._failures.clear()
    wx._circuit._open_since.clear()
    yield
    wx._client = None
    wx._circuit._failures.clear()
    wx._circuit._open_since.clear()


@pytest.fixture()
def env_vars(monkeypatch):
    monkeypatch.setenv("WATSONX_API_KEY", "test-api-key")
    monkeypatch.setenv("WATSONX_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_client(env_vars):
    from services.watsonx import WatsonxClient

    return WatsonxClient()


def _sdk_response(text: str, ti: int = 10, to: int = 20) -> dict:
    return {
        "results": [
            {
                "generated_text": text,
                "input_token_count": ti,
                "generated_token_count": to,
            }
        ]
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestWatsonxClientInit:
    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("WATSONX_API_KEY", raising=False)
        monkeypatch.setenv("WATSONX_PROJECT_ID", "pid")
        from services.watsonx import WatsonxClient

        with pytest.raises(RuntimeError, match="WATSONX_API_KEY"):
            WatsonxClient()

    def test_raises_without_project_id(self, monkeypatch):
        monkeypatch.setenv("WATSONX_API_KEY", "key")
        monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
        from services.watsonx import WatsonxClient

        with pytest.raises(RuntimeError, match="WATSONX_PROJECT_ID"):
            WatsonxClient()

    async def test_raises_on_unknown_tier(self, env_vars):
        client = _make_client(env_vars)
        with pytest.raises(KeyError, match="nosuchmodel"):
            await client.generate("nosuchmodel", "hi")


class TestSuccessfulGenerate:
    async def test_returns_text_and_tokens(self, env_vars):
        client = _make_client(env_vars)

        async def _fake_call_sdk(tier, prompt, **kw):
            return "hello world", 5, 10

        with patch.object(client, "_call_sdk", side_effect=_fake_call_sdk):
            text, ti, to = await client.generate("routing", "test prompt")

        assert text == "hello world"
        assert ti == 5
        assert to == 10


class TestRetry:
    async def test_retries_on_transient_error(self, env_vars):
        """generate() should retry up to 3 times on transient failures."""
        client = _make_client(env_vars)
        call_count = 0

        async def _flaky_call(tier, prompt, **kw):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "recovered", 1, 2

        with patch.object(client, "_call_sdk", side_effect=_flaky_call):
            text, _, _ = await client.generate("routing", "prompt")

        assert text == "recovered"
        assert call_count == 3

    async def test_raises_after_max_retries(self, env_vars):
        """generate() should raise RuntimeError after 3 failed attempts."""
        client = _make_client(env_vars)

        async def _always_fail(tier, prompt, **kw):
            raise ConnectionError("always broken")

        with patch.object(client, "_call_sdk", side_effect=_always_fail):
            with pytest.raises(RuntimeError, match="after retries"):
                await client.generate("routing", "prompt")


class TestCircuitBreaker:
    async def test_circuit_opens_after_threshold(self, env_vars):
        """After 5 consecutive failures, the circuit should open."""
        from services.watsonx import _CIRCUIT_FAILURE_THRESHOLD, CircuitOpenError

        client = _make_client(env_vars)

        async def _always_fail(tier, prompt, **kw):
            raise ConnectionError("broken")

        with patch.object(client, "_call_sdk", side_effect=_always_fail):
            for _ in range(_CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises((RuntimeError, CircuitOpenError)):
                    await client.generate("routing", "p")

        # Next call should raise CircuitOpenError without hitting SDK
        with pytest.raises(CircuitOpenError):
            await client.generate("routing", "p")

    async def test_circuit_reset_after_timeout(self, env_vars, monkeypatch):
        """Circuit should reset after _CIRCUIT_RESET_SECONDS have elapsed."""
        import time

        import services.watsonx as wx

        # Manually open the circuit
        wx._circuit._open_since["routing"] = time.monotonic() - (wx._CIRCUIT_RESET_SECONDS + 1)
        wx._circuit._failures["routing"] = wx._CIRCUIT_FAILURE_THRESHOLD

        client = _make_client(env_vars)
        call_count = 0

        async def _ok(tier, prompt, **kw):
            nonlocal call_count
            call_count += 1
            return "ok", 1, 1

        with patch.object(client, "_call_sdk", side_effect=_ok):
            text, _, _ = await client.generate("routing", "p")

        assert text == "ok"
        assert call_count == 1
