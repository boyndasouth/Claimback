"""Agent 5 — Denial Classifier (§5).

Takes a (redacted) :class:`~app.models.ExtractedClaim` and uses the Granite
``routing`` tier to classify the denial into one of the six
:class:`~app.models.DenialCategory` values, together with a one-sentence rationale.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models import AgentResult, CaseContext, DenialCategory, ExtractedClaim, SourceRef
from services.tracing import trace
from services.json_utils import extract_json_block
from services.watsonx import get_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_JINJA_ENV = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)

# ── JSON cleanup helper ───────────────────────────────────────────────────────


def _extract_json_block(text: str) -> str:
    """Return the first complete JSON value in *text* (see services.json_utils)."""
    return extract_json_block(text)


# ── Agent ─────────────────────────────────────────────────────────────────────


class DenialClassifier:
    """Agent 5 — Denial Classifier.

    Returns the most specific :class:`~app.models.DenialCategory` for a denial
    together with a brief rationale string.
    """

    name = "denial_classifier"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: ExtractedClaim,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[tuple[DenialCategory, str]]:
        """Classify the denial category of *input*.

        Parameters
        ----------
        input:
            Redacted claim (must not contain raw PII).
        ctx:
            Ambient case context.

        Returns
        -------
        AgentResult[tuple[DenialCategory, str]]
            ``output`` is ``(category, rationale)`` where *category* is a valid
            :class:`~app.models.DenialCategory` and *rationale* is ≤ 30 words.
        """
        start = time.monotonic()

        # ── Render prompt ─────────────────────────────────────────────────────
        template = _JINJA_ENV.get_template("denial_classifier/v1.md")
        claim_json = input.model_dump_json(indent=2)
        raw_template = template.render(redacted_claim_json=claim_json)
        prompt = re.sub(r"^---\n.*?\n---\n", "", raw_template, flags=re.DOTALL).strip()

        # ── Call watsonx ──────────────────────────────────────────────────────
        client = get_client()
        generated_text, tokens_in, tokens_out = await client.generate(
            model_tier="routing",
            prompt=prompt,
            max_new_tokens=256,
        )

        # ── Parse response ────────────────────────────────────────────────────
        json_str = _extract_json_block(generated_text)
        try:
            parsed = json.loads(json_str)
            category = DenialCategory(parsed["category"])
            rationale: str = str(parsed.get("rationale", ""))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise ValueError(
                f"DenialClassifier: could not parse model response as classification JSON: {exc!r}\n"
                f"Raw response: {generated_text!r}"
            ) from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        return AgentResult[tuple[DenialCategory, str]](
            output=(category, rationale),
            confidence=0.9,  # classification confidence — updated when we add log-probs
            sources=[],
            trace_id=ctx.trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
