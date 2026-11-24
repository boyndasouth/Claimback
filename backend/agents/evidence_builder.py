"""Agent 9 — Evidence Builder (§5).

Takes a denial category, retrieved policy clauses, and code issues and uses the
Granite ``routing`` tier to produce a structured :class:`~app.models.EvidenceChecklist`
— an ordered list of evidence items the patient should gather before the appeal
letter is written.
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
    EvidenceBuilderInput,
    EvidenceChecklist,
    EvidenceItem,
    SourceRef,
)
from services.tracing import trace
from services.json_utils import extract_json_block
from services.watsonx import get_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_JINJA_ENV = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)


def _extract_json_block(text: str) -> str:
    """Return the first complete JSON value in *text* (see services.json_utils)."""
    return extract_json_block(text)


class EvidenceBuilder:
    """Agent 9 — Evidence Builder.

    Returns an :class:`~app.models.EvidenceChecklist` listing evidence items
    prioritised by their impact on the appeal.
    """

    name = "evidence_builder"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: EvidenceBuilderInput,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[EvidenceChecklist]:
        """Build an evidence checklist for *input*.

        Parameters
        ----------
        input:
            Denial category + policy clauses + code issues from the ANALYZING stage.
        ctx:
            Ambient case context.
        """
        start = time.monotonic()

        # ── Render prompt ─────────────────────────────────────────────────────
        template = _JINJA_ENV.get_template("evidence_builder/v1.md")
        raw_template = template.render(
            category=input.category.value,
            clauses_json=json.dumps(
                [c.model_dump() for c in input.clauses], indent=2, default=str
            ),
            issues_json=json.dumps(
                [i.model_dump() for i in input.issues], indent=2, default=str
            ),
        )
        # Strip YAML front-matter
        prompt = re.sub(r"^---\n.*?\n---\n", "", raw_template, flags=re.DOTALL).strip()

        # ── Call watsonx ──────────────────────────────────────────────────────
        client = get_client()
        generated_text, tokens_in, tokens_out = await client.generate(
            model_tier="routing",
            prompt=prompt,
            max_new_tokens=512,
        )

        # ── Parse response ────────────────────────────────────────────────────
        json_str = _extract_json_block(generated_text)
        try:
            parsed = json.loads(json_str)
            items = [EvidenceItem(**item) for item in parsed["items"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"EvidenceBuilder: could not parse model response: {exc!r}\n"
                f"Raw response: {generated_text!r}"
            ) from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        return AgentResult[EvidenceChecklist](
            output=EvidenceChecklist(items=items),
            confidence=0.85,
            sources=[],
            trace_id=ctx.trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
