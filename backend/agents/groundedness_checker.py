"""Agent 11 — Groundedness Checker (§5, §5.2).

Uses the Granite Guardian model tier to verify that every sentence in the
appeal draft is grounded in a cited source.  Returns a
:class:`~app.models.GroundingReport` whose ``passed`` flag drives the
Writer ⇄ Guardian self-correction loop.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models import (
    AgentResult,
    CaseContext,
    CheckerInput,
    FlaggedSpan,
    GroundingReport,
    SourceRef,
)
from services.json_utils import extract_json_block
from services.tracing import trace
from services.watsonx import get_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_JINJA_ENV = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)


def _extract_json_block(text: str) -> str:
    """Return the first complete JSON value in *text* (see services.json_utils)."""
    return extract_json_block(text)


def _count_sentences(text: str) -> int:
    """Very rough sentence count for groundedness_score denominator."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return max(len([s for s in sentences if s.strip()]), 1)


class GroundednessChecker:
    """Agent 11 — Groundedness Checker.

    Calls the ``guardian`` model tier with the draft and all cited sources.
    Returns a :class:`GroundingReport`.  If the Guardian API is unavailable,
    falls back to the ``routing`` tier (SPEC §20 fallback).
    """

    name = "groundedness_checker"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: CheckerInput,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[GroundingReport]:
        """Check the groundedness of *input.draft* against *input.sources*.

        Parameters
        ----------
        input:
            Draft to check plus the SourceRef list.
        ctx:
            Ambient case context.
        """
        start = time.monotonic()

        # ── Render prompt ─────────────────────────────────────────────────────
        template = _JINJA_ENV.get_template("groundedness_checker/v1.md")
        # Build a sources dict keyed by S# for template rendering
        sources_for_template: dict[str, SourceRef] = dict(input.draft.citations)
        # Also include any extra sources passed directly (not yet cited)
        for i, src in enumerate(input.sources, start=len(sources_for_template) + 1):
            key = f"S{i}"
            if key not in sources_for_template:
                sources_for_template[key] = src

        raw_template = template.render(
            body_markdown=input.draft.body_markdown,
            sources={k: v.model_dump() for k, v in sources_for_template.items()},
        )
        prompt = re.sub(r"^---\n.*?\n---\n", "", raw_template, flags=re.DOTALL).strip()

        # ── Call watsonx (guardian tier; fall back to routing) ────────────────
        client = get_client()
        try:
            generated_text, tokens_in, tokens_out = await client.generate(
                model_tier="guardian",
                prompt=prompt,
                max_new_tokens=512,
            )
        except Exception as exc:
            logger.warning(
                "GroundednessChecker: guardian tier unavailable (%s), "
                "falling back to routing tier.",
                exc,
            )
            generated_text, tokens_in, tokens_out = await client.generate(
                model_tier="routing",
                prompt=prompt,
                max_new_tokens=512,
            )

        # ── Parse response ────────────────────────────────────────────────────
        json_str = _extract_json_block(generated_text)
        try:
            parsed = json.loads(json_str)
            flagged_spans = [
                FlaggedSpan(**span) for span in parsed.get("flagged_spans", [])
            ]
            # Use model-provided score if present; else compute from sentence count
            if "groundedness_score" in parsed:
                groundedness_score = float(parsed["groundedness_score"])
            else:
                total = _count_sentences(input.draft.body_markdown)
                groundedness_score = max(0.0, (total - len(flagged_spans)) / total)
            passed = bool(parsed.get("passed", len(flagged_spans) == 0))
            # Enforce: cannot pass if score < 0.95 or spans exist
            if flagged_spans or groundedness_score < 0.95:
                passed = False
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"GroundednessChecker: could not parse model response: {exc!r}\n"
                f"Raw: {generated_text!r}"
            ) from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        return AgentResult[GroundingReport](
            output=GroundingReport(
                passed=passed,
                flagged_spans=flagged_spans,
                groundedness_score=groundedness_score,
            ),
            confidence=groundedness_score,
            sources=[],
            trace_id=ctx.trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
