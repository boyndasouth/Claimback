"""Tracing decorator for agent runs (§11).

Wraps ``Agent.run()`` to:
- Record latency, token counts, and input/output hashes.
- Persist an ``AgentRun`` row to Postgres via :data:`~db.AsyncSessionFactory`.
- Optionally forward a span to Langfuse (graceful no-op if creds are absent).

Usage::

    class MyAgent:
        name = "my_agent"

        @trace(prompt_version="v1")
        async def run(self, input, ctx):
            ...
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, TypeVar

# Import at module level so tests can monkeypatch these names on this module.
from db import AsyncSessionFactory  # noqa: E402
from db.models import AgentRun  # noqa: E402

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])

# ── Cost estimate (rough; update when real pricing is available) ───────────────
_COST_PER_1K_INPUT = 0.0001   # USD per 1 000 input tokens
_COST_PER_1K_OUTPUT = 0.0003  # USD per 1 000 output tokens


def _hash(obj: Any) -> str:
    """SHA-256 of the JSON-serialised object, truncated to 16 hex chars."""
    raw = json.dumps(obj, default=str, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cost(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in / 1000) * _COST_PER_1K_INPUT + (tokens_out / 1000) * _COST_PER_1K_OUTPUT


# ── Langfuse singleton ────────────────────────────────────────────────────────

_langfuse: Any = None
_langfuse_checked = False


def _get_langfuse() -> Any | None:
    """Return a Langfuse client if credentials are configured, else None."""
    global _langfuse, _langfuse_checked
    if _langfuse_checked:
        return _langfuse
    _langfuse_checked = True

    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not pub or not sec:
        return None

    try:
        from langfuse import Langfuse  # type: ignore[import]

        _langfuse = Langfuse(public_key=pub, secret_key=sec)
        logger.info("Langfuse tracing enabled")
    except ImportError:
        logger.warning("langfuse package not installed; traces will only go to Postgres + console")
    return _langfuse


# ── Decorator ─────────────────────────────────────────────────────────────────


def trace(prompt_version: str = "v1") -> Callable[[F], F]:
    """Decorator factory for agent ``run()`` methods.

    Parameters
    ----------
    prompt_version:
        The version string recorded with each run (e.g. ``"v1"``).

    Example::

        @trace(prompt_version="v1")
        async def run(self, input, ctx):
            ...
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(self: Any, input: Any, ctx: Any) -> Any:  # noqa: A002
            # Use module-level names so they can be monkeypatched in tests.
            import services.tracing as _self_module

            _AsyncSessionFactory = _self_module.AsyncSessionFactory
            _AgentRun = _self_module.AgentRun

            agent_name: str = getattr(self, "name", fn.__qualname__)
            input_hash = _hash(input)
            trace_id = ctx.trace_id if ctx else str(uuid.uuid4())
            start = time.monotonic()
            tokens_in = 0
            tokens_out = 0
            output_obj: Any = None
            exc_to_raise: BaseException | None = None

            try:
                result = await fn(self, input, ctx)
                output_obj = result
                tokens_in = getattr(result, "tokens_in", 0)
                tokens_out = getattr(result, "tokens_out", 0)
                return result
            except BaseException as exc:
                exc_to_raise = exc
                raise
            finally:
                latency_ms = int((time.monotonic() - start) * 1000)
                cost_usd = _cost(tokens_in, tokens_out)

                output_payload: dict[str, Any]
                if exc_to_raise is not None:
                    output_payload = {"error": str(exc_to_raise)}
                elif output_obj is not None:
                    try:
                        output_payload = json.loads(
                            output_obj.model_dump_json() if hasattr(output_obj, "model_dump_json") else json.dumps(output_obj, default=str)
                        )
                    except Exception:
                        output_payload = {"raw": str(output_obj)}
                else:
                    output_payload = {}

                # Persist to Postgres
                try:
                    async with _AsyncSessionFactory() as session:
                        run = _AgentRun(
                            id=str(uuid.uuid4()),
                            case_id=ctx.case_id if ctx else "unknown",
                            agent=agent_name,
                            input_hash=input_hash,
                            output=output_payload,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            latency_ms=latency_ms,
                            cost_usd=cost_usd,
                            trace_id=trace_id,
                        )
                        session.add(run)
                        await session.commit()
                except Exception as db_exc:
                    logger.warning("Failed to persist AgentRun: %s", db_exc)

                # Forward to Langfuse (best-effort)
                lf = _get_langfuse()
                if lf is not None:
                    try:
                        lf_trace = lf.trace(
                            name=agent_name,
                            id=trace_id,
                            metadata={
                                "prompt_version": prompt_version,
                                "input_hash": input_hash,
                                "tokens_in": tokens_in,
                                "tokens_out": tokens_out,
                                "latency_ms": latency_ms,
                                "cost_usd": cost_usd,
                            },
                        )
                        lf_trace.update(output=output_payload)
                    except Exception as lf_exc:
                        logger.warning("Langfuse span failed: %s", lf_exc)
                else:
                    logger.debug(
                        "agent=%s latency_ms=%d tokens_in=%d tokens_out=%d cost_usd=%.6f",
                        agent_name,
                        latency_ms,
                        tokens_in,
                        tokens_out,
                        cost_usd,
                    )

        return wrapper  # type: ignore[return-value]

    return decorator
