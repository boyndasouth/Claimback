"""CLI script to record replay fixtures from live pipeline runs (Day 7).

Usage:
    python -m scripts.record_replay --case-ids <id1> <id2> ...

For each case_id the script:
1. Queries Postgres for the case row, all agent_runs, the latest draft, and the
   case summary.
2. Assembles a fixture JSON matching the shapes consumed by the frontend
   (CaseStateResponse, TraceResponse, CaseSummary, DraftResponse), plus a
   synthetic ``events`` array of SSE-style progress events with relative
   timestamps (ms from start) so replay mode can play back the live progress
   view.
3. Writes ``replay/<case_id>.json`` (UTF-8, 2-space indent).  Existing files are
   overwritten.

Prerequisites:
    DATABASE_URL env var must point to the running Postgres instance.
    Run ``make migrate`` before first use.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib
import sys

from sqlalchemy import desc, select

# Allow running as `python -m scripts.record_replay` from repo root or
# as `python scripts/record_replay.py` from repo root.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from db import AsyncSessionFactory
from db.models import AgentRun, Case, Draft
from services.case_summary import get_case_summary

logger = logging.getLogger("record_replay")

_REPLAY_DIR = _REPO_ROOT / "replay"


# ── Status ordering for synthetic SSE event replay ────────────────────────────

_PIPELINE_STATUSES = [
    "EXTRACTING",
    "ANALYZING",
    "DRAFTING",
    "VERIFYING",
    "AWAITING_APPROVAL",
]

# Rough ms between synthetic SSE events (spread across a 60 s window)
_STEP_MS = 60_000 // max(len(_PIPELINE_STATUSES), 1)


def _make_events(case_status: str, success_prob: float | None) -> list[dict]:
    """Return a synthetic SSE event list with relative ms timestamps.

    The events array mirrors what the SSE stream emits during a live pipeline
    run so the frontend's replay mode can replay the progress stepper.
    """
    events: list[dict] = []
    elapsed = 0

    # Emit each intermediate status up to the final recorded status
    for step_status in _PIPELINE_STATUSES:
        events.append({"elapsed_ms": elapsed, "type": "status", "status": step_status})
        elapsed += _STEP_MS
        if step_status == case_status:
            break

    # Terminal "done" event when pipeline succeeded
    if case_status == "AWAITING_APPROVAL":
        events.append({
            "elapsed_ms": elapsed,
            "type": "done",
            "status": "AWAITING_APPROVAL",
            "success_prob": success_prob,
        })
    elif case_status == "NEEDS_HUMAN_REVIEW":
        events.append({
            "elapsed_ms": elapsed,
            "type": "needs_review",
            "status": "NEEDS_HUMAN_REVIEW",
        })

    return events


async def _record_case(case_id: str) -> None:
    """Query DB and write replay/<case_id>.json."""
    async with AsyncSessionFactory() as session:
        # ── Case row ──────────────────────────────────────────────────────────
        case_result = await session.execute(select(Case).where(Case.id == case_id))
        case = case_result.scalar_one_or_none()
        if case is None:
            logger.error("Case %s not found — skipping", case_id)
            return

        # ── Agent runs → trace ────────────────────────────────────────────────
        runs_result = await session.execute(
            select(AgentRun)
            .where(AgentRun.case_id == case_id)
            .order_by(AgentRun.latency_ms)
        )
        runs = runs_result.scalars().all()

        trace_agents = [
            {
                "agent": r.agent,
                "input_hash": r.input_hash,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "latency_ms": r.latency_ms,
                "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
                "trace_id": r.trace_id,
            }
            for r in runs
        ]

        # ── Latest draft ──────────────────────────────────────────────────────
        draft_result = await session.execute(
            select(Draft)
            .where(Draft.case_id == case_id)
            .order_by(desc(Draft.version))
            .limit(1)
        )
        draft_row = draft_result.scalar_one_or_none()

        draft_fixture: dict | None = None
        if draft_row is not None:
            citations: dict = {}
            if draft_row.grounding_report and isinstance(draft_row.grounding_report, dict):
                citations = draft_row.grounding_report.get("citations", {})
            draft_fixture = {
                "case_id": case_id,
                "version": draft_row.version,
                "body_markdown": draft_row.body,
                "citations": citations,
                "grounding_report": draft_row.grounding_report,
            }

        # ── Case summary ──────────────────────────────────────────────────────
        summary = await get_case_summary(case_id, session)
        summary_dict = summary.model_dump() if summary is not None else {}

        # ── Assemble fixture ──────────────────────────────────────────────────
        fixture = {
            "case": {
                "case_id": case.id,
                "status": case.status,
                "category": case.category,
                "success_prob": case.success_prob,
                "draft": draft_row.body if draft_row else None,
                "draft_version": draft_row.version if draft_row else None,
                "grounding_passed": (
                    bool(draft_row.grounding_report.get("passed"))
                    if draft_row and draft_row.grounding_report
                    else None
                ),
            },
            "trace": {
                "case_id": case_id,
                "agents": trace_agents,
            },
            "summary": summary_dict,
            "draft": draft_fixture,
            "events": _make_events(case.status, case.success_prob),
        }

    # ── Write file ────────────────────────────────────────────────────────────
    _REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _REPLAY_DIR / f"{case_id}.json"
    out_path.write_text(json.dumps(fixture, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s (%d bytes)", out_path, out_path.stat().st_size)


async def _main(case_ids: list[str]) -> int:
    errors = 0
    for case_id in case_ids:
        try:
            await _record_case(case_id)
        except Exception as exc:
            logger.error("Failed to record case %s: %s", case_id, exc)
            errors += 1
    return errors


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Record replay fixtures from live pipeline runs.",
    )
    parser.add_argument(
        "--case-ids",
        nargs="+",
        required=True,
        metavar="CASE_ID",
        help="One or more case UUIDs to record.",
    )
    args = parser.parse_args()
    errors = asyncio.run(_main(args.case_ids))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
