"""Agent 10 — Appeal Writer (§5).

Produces an :class:`~app.models.AppealDraft` with inline ``[S#]`` citations.
Accepts either a :class:`~app.models.WriterInput` (first pass) or a
:class:`~app.models.WriterRetryInput` (rewrite after Guardian flags spans).
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
    AppealDraft,
    CaseContext,
    FlaggedSpan,
    SourceRef,
    WriterInput,
    WriterRetryInput,
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


def _clean_body(body: str) -> str:
    """Collapse degenerate repetition the model sometimes emits.

    Runs of empty blockquote lines ("> ") and 3+ blank lines are squashed.
    """
    body = re.sub(r"(?:\n>[ \t]*(?=\n)){2,}", "\n>", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _citation_keys_in_body(body: str) -> set[str]:
    """Return all [S#] tags found in body_markdown."""
    return set(re.findall(r"\[S\d+\]", body))


class AppealWriter:
    """Agent 10 — Appeal Writer.

    On first call takes a :class:`WriterInput`; on rewrites takes a
    :class:`WriterRetryInput` that includes the Guardian's flagged spans.
    Returns an :class:`AppealDraft` with version incremented from the previous.
    """

    name = "appeal_writer"

    @trace(prompt_version="v2")
    async def run(
        self,
        input: WriterInput | WriterRetryInput,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[AppealDraft]:
        """Write (or rewrite) the appeal letter.

        Parameters
        ----------
        input:
            :class:`WriterInput` for first draft or :class:`WriterRetryInput`
            for a correction pass.
        ctx:
            Ambient case context.
        """
        start = time.monotonic()
        is_retry = isinstance(input, WriterRetryInput)
        base = input.base if is_retry else input
        previous_version = input.previous_draft.version if is_retry else 0
        flagged_spans: list[FlaggedSpan] = input.flagged_spans if is_retry else []

        # ── Number the retrieved clauses S1..Sn ───────────────────────────────
        # The model cites by key only; citations are built here from the real
        # clauses, so it can't invent or misquote a source.
        source_map: dict[str, SourceRef] = {
            f"S{i}": SourceRef(
                clause_id=c.clause_id, doc_id=c.doc_id, section=c.section, text=c.text
            )
            for i, c in enumerate(base.clauses, start=1)
        }
        claim = base.claim

        # ── Render prompt ─────────────────────────────────────────────────────
        template = _JINJA_ENV.get_template("appeal_writer/v2.md")
        raw_template = template.render(
            is_retry=is_retry,
            flagged_spans=flagged_spans,
            previous_draft_body=input.previous_draft.body_markdown if is_retry else "",
            category=claim.denial_reason_text,
            claim_facts={
                "claim_id": claim.claim_id,
                "payer": claim.payer,
                "plan_type": claim.plan_type,
                "service_date": str(claim.service_date),
                "denial_date": str(claim.denial_date),
                "cpt_codes": ", ".join(claim.cpt_codes),
                "icd10_codes": ", ".join(claim.icd10_codes),
                "billed_amount": str(claim.billed_amount),
                "denial_reason_code": claim.denial_reason_code or "not stated",
            },
            sources=[
                {"key": k, "section": s.section, "text": s.text}
                for k, s in source_map.items()
            ],
            checklist_json=json.dumps(
                base.checklist.model_dump(), indent=2, default=str
            ),
            issues_json=json.dumps(
                [i.model_dump() for i in base.issues], indent=2, default=str
            ),
            internal_deadline=str(base.plan.internal_appeal_deadline),
            external_review_available=base.plan.external_review_available,
            external_deadline=str(base.plan.external_review_deadline)
            if base.plan.external_review_deadline
            else None,
            expedited_eligible=base.plan.expedited_eligible,
        )
        prompt = re.sub(r"^---\n.*?\n---\n", "", raw_template, flags=re.DOTALL).strip()

        # ── Call watsonx (up to 2 attempts) ───────────────────────────────────
        # Long free-text generation at temperature 0 can fall into a repetition
        # loop (e.g. an endless run of "> " lines) until max_tokens cuts the JSON
        # off. A small temperature + frequency penalty prevents most loops; if
        # the output still doesn't parse, retry once a little warmer.
        client = get_client()
        tokens_in = tokens_out = 0
        body_markdown = ""
        last_exc: Exception | None = None
        generated_text = ""
        for attempt, temperature in enumerate((0.3, 0.6), start=1):
            generated_text, t_in, t_out = await client.generate(
                model_tier="writing",
                prompt=prompt,
                max_new_tokens=2500,
                temperature=temperature,
                frequency_penalty=0.4,
            )
            tokens_in += t_in
            tokens_out += t_out
            try:
                parsed = json.loads(_extract_json_block(generated_text))
                body_markdown = parsed["body_markdown"]
                if not isinstance(body_markdown, str) or not body_markdown.strip():
                    raise ValueError("empty body_markdown")
                break
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_exc = exc
                logger.warning(
                    "AppealWriter attempt %d: unparseable output (%r, %d chars) — %s",
                    attempt, exc, len(generated_text),
                    "retrying" if attempt == 1 else "giving up",
                )
        else:
            raise ValueError(
                f"AppealWriter: could not parse model response after 2 attempts: "
                f"{last_exc!r}\nRaw (start): {generated_text[:1000]!r}"
            ) from last_exc

        body_markdown = _clean_body(body_markdown)

        # Citations come only from the retrieved clauses, never from the model.
        used = {t.strip("[]") for t in _citation_keys_in_body(body_markdown)}
        citations: dict[str, SourceRef] = {
            k: source_map[k] for k in sorted(used) if k in source_map
        }
        unknown = used - set(source_map)
        if unknown:
            logger.warning(
                "AppealWriter: draft cites %s, which are not retrieved sources — "
                "the groundedness check will flag those sentences.",
                unknown,
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        return AgentResult[AppealDraft](
            output=AppealDraft(
                body_markdown=body_markdown,
                citations=citations,
                version=previous_version + 1,
            ),
            confidence=0.8,
            sources=list(citations.values()),
            trace_id=ctx.trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
