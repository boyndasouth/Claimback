"""Idempotent agent-run cache (§16).

Provides a single helper, :func:`get_cached_result`, that queries the
``agent_runs`` table for a previous run keyed by ``(case_id, agent, input_hash)``.
If a hit is found the stored output dict is returned and callers can skip the
LLM call entirely.

The ``input_hash`` is computed by :func:`hash_input` (re-exported from
:mod:`services.tracing` for backward compatibility).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from db import AsyncSessionFactory
from db.models import AgentRun
from services.tracing import _hash as hash_input  # re-export

__all__ = ["get_cached_result", "hash_input"]

logger = logging.getLogger(__name__)


async def get_cached_result(
    case_id: str,
    agent_name: str,
    input_hash: str,
) -> dict | None:
    """Return the cached output dict for a previous agent run, or ``None`` if not found.

    Parameters
    ----------
    case_id:
        UUID of the case.
    agent_name:
        The ``Agent.name`` string (e.g. ``"appeal_writer"``).
    input_hash:
        16-hex-char hash produced by :func:`hash_input`.

    Returns
    -------
    dict | None
        The stored ``output`` JSONB if a matching run exists, else ``None``.
    """
    try:
        async with AsyncSessionFactory() as session:
            stmt = select(AgentRun.output).where(
                AgentRun.case_id == case_id,
                AgentRun.agent == agent_name,
                AgentRun.input_hash == input_hash,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                logger.debug(
                    "Cache hit: case=%s agent=%s hash=%s",
                    case_id,
                    agent_name,
                    input_hash,
                )
                return row  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("Cache lookup failed: %s", exc)
    return None
