"""watsonx.ai client with timeout, retry, and per-model circuit breaker (§16).

Usage::

    client = get_client()
    text, tokens_in, tokens_out = await client.generate("routing", prompt)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ── Model IDs (never hard-coded — read from config once at import time) ───────

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "models.yaml"


def _load_model_ids() -> dict[str, str]:
    with open(_CONFIG_PATH) as fh:
        data: dict[str, str] = yaml.safe_load(fh)
    return data


MODEL_IDS: dict[str, str] = _load_model_ids()

# ── Circuit breaker ───────────────────────────────────────────────────────────

_CIRCUIT_FAILURE_THRESHOLD = 5
_CIRCUIT_RESET_SECONDS = 60.0


class CircuitOpenError(RuntimeError):
    """Raised when a model's circuit breaker is open (too many recent failures)."""


class _CircuitBreaker:
    """Per-model circuit breaker with a time-based reset."""

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self._open_since: dict[str, float] = {}

    def check(self, tier: str) -> None:
        """Raise CircuitOpenError if the circuit is open for *tier*."""
        if tier not in self._open_since:
            return
        elapsed = time.monotonic() - self._open_since[tier]
        if elapsed >= _CIRCUIT_RESET_SECONDS:
            # Half-open: reset and allow the next attempt
            del self._open_since[tier]
            self._failures[tier] = 0
            logger.info("Circuit breaker for '%s' reset after %.0f s", tier, elapsed)
        else:
            raise CircuitOpenError(
                f"Circuit open for model tier '{tier}' — {_CIRCUIT_RESET_SECONDS - elapsed:.0f} s until reset"
            )

    def record_failure(self, tier: str) -> None:
        self._failures[tier] = self._failures.get(tier, 0) + 1
        if self._failures[tier] >= _CIRCUIT_FAILURE_THRESHOLD:
            self._open_since[tier] = time.monotonic()
            logger.error(
                "Circuit breaker OPEN for model tier '%s' after %d failures",
                tier,
                self._failures[tier],
            )

    def record_success(self, tier: str) -> None:
        self._failures[tier] = 0
        self._open_since.pop(tier, None)


_circuit = _CircuitBreaker()

# ── Retry policy ──────────────────────────────────────────────────────────────

_TIMEOUT_SECONDS = 30.0


def _is_transient(exc: BaseException) -> bool:
    """Return True for errors that are worth retrying."""
    # Import lazily so we can mock without the SDK installed
    try:
        from ibm_watsonx_ai.foundation_models.utils.exceptions import (
            ModelInferenceException,
        )

        if isinstance(exc, ModelInferenceException):
            return True
    except ImportError:
        pass
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError))


# ── WatsonxClient ─────────────────────────────────────────────────────────────


class WatsonxClient:
    """Thin async wrapper around ibm-watsonx-ai for text generation.

    Singleton via :func:`get_client`.  Credentials are read from env vars::

        WATSONX_API_KEY, WATSONX_PROJECT_ID, WATSONX_URL
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("WATSONX_API_KEY", "")
        self._project_id = os.environ.get("WATSONX_PROJECT_ID", "")
        self._url = os.environ.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")

        if not self._api_key or not self._project_id:
            raise RuntimeError(
                "WATSONX_API_KEY and WATSONX_PROJECT_ID must be set before initialising WatsonxClient"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        model_tier: str,
        prompt: str,
        **kwargs: Any,
    ) -> tuple[str, int, int]:
        """Generate text via watsonx.ai.

        Parameters
        ----------
        model_tier:
            One of the keys in ``config/models.yaml`` (e.g. ``"routing"``).
        prompt:
            The rendered prompt string.
        **kwargs:
            Extra generation parameters forwarded to the SDK (e.g.
            ``max_new_tokens``, ``temperature``).

        Returns
        -------
        tuple[str, int, int]
            ``(generated_text, tokens_in, tokens_out)``

        Raises
        ------
        KeyError
            If *model_tier* is not in ``models.yaml``.
        CircuitOpenError
            If the circuit breaker is open for this tier.
        RuntimeError
            After 3 retries all fail.
        """
        if model_tier not in MODEL_IDS:
            raise KeyError(f"Unknown model tier '{model_tier}'. Known: {list(MODEL_IDS)}")

        _circuit.check(model_tier)

        try:
            result = await self._generate_with_retry(model_tier, prompt, **kwargs)
            _circuit.record_success(model_tier)
            return result
        except RetryError as exc:
            _circuit.record_failure(model_tier)
            raise RuntimeError(
                f"watsonx generate failed for tier '{model_tier}' after retries"
            ) from exc

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the ``embedding`` model tier.

        Returns a list of float vectors, one per input text.  The embedding
        model is ``ibm/granite-embedding-278m-multilingual`` (768 dimensions).

        Parameters
        ----------
        texts:
            Non-empty list of strings to embed.

        Returns
        -------
        list[list[float]]
            One 768-dimensional vector per input text.

        Raises
        ------
        CircuitOpenError
            If the embedding circuit breaker is open.
        RuntimeError
            After 3 retries all fail.
        """
        tier = "embedding"
        if tier not in MODEL_IDS:
            raise KeyError(f"Model tier '{tier}' not found in models.yaml")

        _circuit.check(tier)

        @retry(
            retry=retry_if_exception_type(Exception),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=False,
        )
        async def _attempt() -> list[list[float]]:
            return await asyncio.wait_for(
                self._call_embed(tier, texts),
                timeout=_TIMEOUT_SECONDS,
            )

        try:
            vectors: list[list[float]] = await _attempt()
            _circuit.record_success(tier)
            return vectors
        except RetryError as exc:
            _circuit.record_failure(tier)
            raise RuntimeError("watsonx embed failed after retries") from exc

    async def _call_embed(self, tier: str, texts: list[str]) -> list[list[float]]:
        """Run the synchronous ibm-watsonx-ai Embeddings call in a thread."""
        model_id = MODEL_IDS[tier]

        def _sync_embed() -> list[list[float]]:
            from ibm_watsonx_ai import APIClient, Credentials
            from ibm_watsonx_ai.foundation_models.embeddings import (
                Embeddings,
            )

            creds = Credentials(url=self._url, api_key=self._api_key)
            client = APIClient(credentials=creds, project_id=self._project_id)
            # Safety net for the model's 512-token limit: truncate instead of 400.
            emb = Embeddings(
                model_id=model_id,
                api_client=client,
                params={"truncate_input_tokens": 512},
            )
            response = emb.embed_documents(texts)
            # response is list[list[float]] (one vector per text)
            return response

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_embed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _generate_with_retry(
        self,
        model_tier: str,
        prompt: str,
        **kwargs: Any,
    ) -> tuple[str, int, int]:
        """Call the SDK inside tenacity retry + 30 s timeout."""

        @retry(
            retry=retry_if_exception_type(Exception),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=False,
        )
        async def _attempt() -> tuple[str, int, int]:
            return await asyncio.wait_for(
                self._call_sdk(model_tier, prompt, **kwargs),
                timeout=_TIMEOUT_SECONDS,
            )

        return await _attempt()

    async def _call_sdk(
        self,
        model_tier: str,
        prompt: str,
        **kwargs: Any,
    ) -> tuple[str, int, int]:
        """Actual SDK call — runs the synchronous ibm-watsonx-ai in a thread."""
        model_id = MODEL_IDS[model_tier]

        def _sync_call() -> tuple[str, int, int]:
            from ibm_watsonx_ai import APIClient, Credentials
            from ibm_watsonx_ai.foundation_models import ModelInference

            creds = Credentials(url=self._url, api_key=self._api_key)
            client = APIClient(credentials=creds, project_id=self._project_id)
            model = ModelInference(model_id=model_id, api_client=client)

            # Chat API (/ml/v1/text/chat). The old /text/generation endpoint is
            # deprecated and feeds instruct models a raw string, so they tend to
            # continue the document instead of following the instructions.
            response = model.chat(
                messages=[{"role": "user", "content": prompt}],
                params=_to_chat_params(kwargs),
            )

            generated = response["choices"][0]["message"].get("content") or ""
            usage = response.get("usage") or {}
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            return generated, tokens_in, tokens_out

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)


# ── Chat parameter mapping ───────────────────────────────────────────────────

_CHAT_PARAM_NAMES = {
    "frequency_penalty", "presence_penalty", "temperature", "max_tokens",
    "top_p", "seed", "stop", "repetition_penalty", "response_format", "time_limit",
}


def _to_chat_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Translate the generation-style kwargs agents pass into chat parameters.

    ``max_new_tokens`` → ``max_tokens``, ``stop_sequences`` → ``stop``,
    ``json_mode=True`` → ``response_format={"type": "json_object"}``.
    Temperature defaults to 0 so extraction/classification are repeatable.
    Unknown keys (e.g. ``decoding_method``) are dropped.
    """
    params: dict[str, Any] = {"max_tokens": 2048, "temperature": 0}
    for key, value in kwargs.items():
        if key == "max_new_tokens":
            params["max_tokens"] = value
        elif key == "stop_sequences":
            params["stop"] = value
        elif key == "json_mode":
            if value:
                params["response_format"] = {"type": "json_object"}
        elif key in _CHAT_PARAM_NAMES:
            params[key] = value
    return params


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: WatsonxClient | None = None


def get_client() -> WatsonxClient:
    """Return the module-level singleton, creating it on first call."""
    global _client
    if _client is None:
        _client = WatsonxClient()
    return _client
