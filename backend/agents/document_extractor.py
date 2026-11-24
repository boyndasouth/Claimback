"""Agent 3 — Document Extractor (§5).

Converts raw denial-letter text (or a PDF path) into a structured
:class:`~app.models.ExtractedClaim`.

Primary path: watsonx.ai text extraction + Granite ``writing`` tier for field mapping.
Fallback path: Docling (open-source PDF parser) used when the watsonx extraction call
fails or when the input is already plain text.

The agent implements the :class:`~app.models.Agent` protocol and is wrapped with the
:func:`~services.tracing.trace` decorator so every run is persisted to ``agent_runs``.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models import AgentResult, CaseContext, ExtractedClaim, SourceRef
from services.tracing import trace
from services.json_utils import extract_json_block
from services.watsonx import get_client

logger = logging.getLogger(__name__)

# Resolve prompts root relative to this file: backend/agents/ → repo-root/prompts/
_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_JINJA_ENV = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)


# ── Docling fallback ──────────────────────────────────────────────────────────


def _extract_text_docling(source: str | bytes) -> str:
    """Extract plain text from a PDF using Docling.

    Parameters
    ----------
    source:
        Either a file-system path string or raw PDF bytes.

    Returns
    -------
    str
        The extracted plain text.

    Raises
    ------
    RuntimeError
        If Docling is not installed or the extraction fails.
    """
    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed.  Add 'docling' to your dependencies."
        ) from exc

    converter = DocumentConverter()
    if isinstance(source, bytes):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        result = converter.convert(tmp_path)
    else:
        result = converter.convert(source)

    return result.document.export_to_markdown()


def _is_file_path(value: str) -> bool:
    """True only if *value* plausibly names an existing file.

    Letter text must never be treated as a path: on Linux (Render) a long
    path component raises ENAMETOOLONG from ``Path.exists`` instead of
    returning False.
    """
    if not value or len(value) > 1024 or "\n" in value:
        return False
    try:
        return Path(value).is_file()
    except (OSError, ValueError):
        return False


# ── JSON cleanup ──────────────────────────────────────────────────────────────


def _extract_json_block(text: str) -> str:
    """Return the first complete JSON value in *text* (see services.json_utils)."""
    return extract_json_block(text)


# ── Agent ─────────────────────────────────────────────────────────────────────


class DocumentExtractor:
    """Agent 3 — Document Extractor.

    Accepts either raw denial-letter text or a path/bytes to a PDF.
    If the string looks like a path to an existing file, it is treated as a
    PDF and passed to Docling first.  Otherwise the text is used directly.
    """

    name = "document_extractor"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: str | bytes,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[ExtractedClaim]:
        """Extract claim fields from a denial document.

        Parameters
        ----------
        input:
            Raw denial-letter text, a filesystem path to a PDF (str), or PDF bytes.
        ctx:
            Ambient case context.

        Returns
        -------
        AgentResult[ExtractedClaim]
        """
        start = time.monotonic()

        # ── Step 1: resolve input to plain text ───────────────────────────────
        denial_text: str
        if isinstance(input, bytes):
            # PDF bytes → Docling
            logger.debug("DocumentExtractor: input is bytes, using Docling")
            denial_text = _extract_text_docling(input)
        elif isinstance(input, str) and _is_file_path(input):
            # Path to PDF → Docling
            logger.debug("DocumentExtractor: input is a file path, using Docling")
            denial_text = _extract_text_docling(input)
        else:
            # Already plain text
            denial_text = str(input)

        # ── Step 2: render prompt ─────────────────────────────────────────────
        template = _JINJA_ENV.get_template("document_extractor/v1.md")
        # Strip YAML front-matter before sending to the model
        raw_template = template.render(denial_text=denial_text)
        prompt = re.sub(r"^---\n.*?\n---\n", "", raw_template, flags=re.DOTALL).strip()

        # ── Step 3: call watsonx (with Docling fallback on parse failure) ─────
        client = get_client()
        generated_text = ""
        tokens_in = 0
        tokens_out = 0
        claim: ExtractedClaim | None = None
        last_exc: Exception | None = None

        # Up to two watsonx attempts; the second only when the model's output
        # failed to parse. Docling is used only in Step 1, for file/bytes input —
        # it cannot parse text that is already extracted.
        attempt_prompt = prompt
        for attempt in range(2):
            try:
                generated_text, tokens_in, tokens_out = await client.generate(
                    model_tier="writing",
                    prompt=attempt_prompt,
                    max_new_tokens=1024,
                )
                json_str = _extract_json_block(generated_text)
                claim = ExtractedClaim.model_validate_json(json_str)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "DocumentExtractor attempt %d failed: %s", attempt + 1, exc
                )
                if isinstance(exc, ValueError) or "JSON" in type(exc).__name__ or "Validation" in type(exc).__name__:
                    # Model answered but output didn't parse/validate: retry once,
                    # telling it exactly what was wrong (repair prompt).
                    attempt_prompt = (
                        f"{prompt}\n\n## Your previous answer was rejected\n"
                        f"Previous output:\n{generated_text[:2000]}\n\n"
                        f"Validation error:\n{str(exc)[:1000]}\n\n"
                        "Return the corrected JSON object only."
                    )
                    continue
                # Transport/auth/config error (e.g. bad project ID): retrying won't help.
                break

        if claim is None:
            raise RuntimeError(
                f"DocumentExtractor could not parse a valid ExtractedClaim after retries: {last_exc}"
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        # Derive a scalar confidence from mean field_confidence values
        confidence = (
            sum(claim.field_confidence.values()) / len(claim.field_confidence)
            if claim.field_confidence
            else 0.5
        )

        return AgentResult[ExtractedClaim](
            output=claim,
            confidence=confidence,
            sources=[],
            trace_id=ctx.trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
