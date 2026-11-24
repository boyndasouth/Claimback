"""Agent 4 — PII Redactor (§5).

Takes an :class:`~app.models.ExtractedClaim`, replaces all personally identifiable
information with deterministic tokens, and stores the token map encrypted in
:class:`~db.models.PiiVault`.

Two-pass strategy (SPEC §5):
1. **Regex pass** — catches high-recall patterns: names embedded in free text, DOB,
   SSN, phone, email, MRN, insurance IDs, NPI.
2. **Granite NER pass** — sends the JSON representation to the ``routing`` tier model
   to catch any residual PII the regex missed.

The ``denial_reason_text`` field (the only free-text field) is the primary target.
Structured fields (codes, dates, amounts) are preserved exactly.

The resulting redacted :class:`~app.models.ExtractedClaim` is safe to pass to all
downstream agents without leaking PII.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models import AgentResult, CaseContext, ExtractedClaim
from services.json_utils import extract_json_block
from services.tracing import trace
from services.watsonx import get_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_JINJA_ENV = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)

# ── Fernet encryption ─────────────────────────────────────────────────────────


def _get_fernet() -> Fernet:  # type: ignore[name-defined]  # noqa: F821
    from cryptography.fernet import Fernet

    key = os.environ.get("PII_VAULT_KEY", "")
    if not key:
        raise RuntimeError("PII_VAULT_KEY environment variable must be set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt_token_map(token_map: dict[str, str]) -> str:
    """Serialize and Fernet-encrypt the token map; return a base64 string."""
    fernet = _get_fernet()
    plaintext = json.dumps(token_map).encode()
    encrypted: str = fernet.encrypt(plaintext).decode()
    return encrypted


# ── Regex PII patterns ────────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, str]] = [
    # Names: "Patient <First Last>", "Member <First Last>", "Subscriber <First Last>"
    # Matches a capitalised two-word name after a patient/member indicator keyword.
    (
        r"(?:Patient|Member|Subscriber|Insured|Claimant|Enrollee)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        "PERSON",
    ),
    # SSN:  123-45-6789
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN"),
    # Phone: (123) 456-7890 / 123-456-7890 / 123.456.7890 / 1234567890
    # Note: use a lookahead/lookbehind approach to avoid \b issues before '('
    (r"(?<!\d)(?:\(\d{3}\)\s*|\d{3}[-.])\d{3}[-.]?\d{4}(?!\d)", "PHONE"),
    # Email
    (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "EMAIL"),
    # NPI: 10-digit number preceded by "NPI" keyword
    (r"\bNPI[:\s#]*\d{10}\b", "NPI"),
    # MRN: MRN / Medical Record # followed by alphanumeric
    (r"\b(?:MRN|Medical\s+Record(?:\s+Number)?)[:\s#]*[A-Za-z0-9\-]{4,20}\b", "MRN"),
    # Insurance member/subscriber IDs
    (r"\b(?:Member|Subscriber|Group)\s*(?:ID|#|No\.?)[:\s]*[A-Za-z0-9\-]{6,20}\b", "INSURANCE_ID"),
    # Street address: number + street
    (r"\b\d{1,5}\s+[A-Za-z0-9\s]{3,30}(?:St|Ave|Blvd|Dr|Rd|Ln|Way|Ct|Pl)\.?\b", "ADDRESS"),
    # DOB patterns: "DOB:", "Date of Birth:", or "DOB MM/DD/YYYY"
    (r"(?:DOB|Date\s+of\s+Birth)[:\s]+\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}", "DOB"),
]


def _apply_regex_pass(text: str, counters: dict[str, int]) -> tuple[str, dict[str, str]]:
    """Replace regex-matched PII spans with tokens; return (redacted_text, token_map)."""
    token_map: dict[str, str] = {}
    for pattern, pii_type in _PII_PATTERNS:
        def replacer(m: re.Match[str], _type: str = pii_type) -> str:
            span = m.group(0)
            if span in token_map:
                return token_map[span]
            counters[_type] += 1
            token = f"[{_type}_{counters[_type]}]"
            token_map[span] = token
            return token
        text = re.sub(pattern, replacer, text, flags=re.IGNORECASE)
    return text, token_map


# ── JSON cleanup ──────────────────────────────────────────────────────────────


_MONEY_OR_NUMBER = re.compile(r"^\$?\s*[\d,]+(\.\d+)?$")


def _clean_ner_map(raw: object) -> dict[str, str]:
    """Keep only usable original→token pairs from the model's NER output.

    Drops non-string entries, very short spans (would over-match), and plain
    money/number values — dollar amounts are not PII and are needed downstream.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for original, token in raw.items():
        if not isinstance(original, str) or not isinstance(token, str):
            continue
        original = original.strip()
        if len(original) < 3 or _MONEY_OR_NUMBER.match(original):
            continue
        cleaned[original] = token
    return cleaned


def _extract_json_block(text: str) -> str:
    """Return the first complete JSON value in *text* (see services.json_utils)."""
    return extract_json_block(text)


# ── Agent ─────────────────────────────────────────────────────────────────────


class PiiRedactor:
    """Agent 4 — PII Redactor.

    Returns a copy of the :class:`~app.models.ExtractedClaim` with all PII
    replaced by tokens.  Also persists the encrypted token map to ``pii_vault``.
    """

    name = "pii_redactor"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: ExtractedClaim,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[ExtractedClaim]:
        """Redact PII from *input* and store token map encrypted in PiiVault.

        Parameters
        ----------
        input:
            Extracted claim (may contain PII in free-text fields).
        ctx:
            Ambient case context.

        Returns
        -------
        AgentResult[ExtractedClaim]
            Output is an ``ExtractedClaim`` with PII replaced by ``[TYPE_N]`` tokens.
        """
        start = time.monotonic()
        counters: dict[str, int] = defaultdict(int)
        token_map: dict[str, str] = {}

        # ── Pass 1: regex on denial_reason_text (the only free-text field) ───
        redacted_text, regex_tokens = _apply_regex_pass(
            input.denial_reason_text, counters
        )
        token_map.update(regex_tokens)

        # Build a working copy of the claim with the regex-redacted text
        claim_dict = input.model_dump(mode="json")
        claim_dict["denial_reason_text"] = redacted_text

        # ── Pass 2: Granite NER on the full (regex-redacted) claim JSON ───────
        template = _JINJA_ENV.get_template("pii_redactor/v1.md")
        raw_template = template.render(claim_json=json.dumps(claim_dict, default=str, indent=2))
        prompt = re.sub(r"^---\n.*?\n---\n", "", raw_template, flags=re.DOTALL).strip()

        client = get_client()
        tokens_in = 0
        tokens_out = 0

        try:
            generated_text, tokens_in, tokens_out = await client.generate(
                model_tier="routing",
                prompt=prompt,
                max_new_tokens=512,
            )
            json_str = _extract_json_block(generated_text)
            ner_map = _clean_ner_map(json.loads(json_str))
            # Merge NER tokens into token_map (NER wins on conflicts)
            token_map.update(ner_map)

            # Apply NER replacements to denial_reason_text ONLY. Structured
            # fields (amounts, dates, codes, enums) are typed and never PII-masked:
            # replacing across the whole JSON corrupted e.g. billed_amount.
            text = claim_dict["denial_reason_text"]
            for original in sorted(ner_map, key=len, reverse=True):
                text = text.replace(original, ner_map[original])
            claim_dict["denial_reason_text"] = text
        except Exception as exc:
            logger.warning(
                "PiiRedactor NER pass failed (using regex-only result): %s", exc
            )

        # ── Encrypt and persist token map ─────────────────────────────────────
        encrypted_map = _encrypt_token_map(token_map)
        await _upsert_pii_vault(ctx.case_id, encrypted_map)

        # ── Build redacted claim ───────────────────────────────────────────────
        redacted_claim = ExtractedClaim.model_validate(claim_dict)

        latency_ms = int((time.monotonic() - start) * 1000)

        return AgentResult[ExtractedClaim](
            output=redacted_claim,
            confidence=1.0,
            sources=[],
            trace_id=ctx.trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )


# ── DB helper ─────────────────────────────────────────────────────────────────


async def _upsert_pii_vault(case_id: str, encrypted_map: str) -> None:
    """Insert or replace the PiiVault row for *case_id*."""
    from sqlalchemy import select

    from db import AsyncSessionFactory
    from db.models import PiiVault

    try:
        async with AsyncSessionFactory() as session:
            stmt = select(PiiVault).where(PiiVault.case_id == case_id)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing is not None:
                existing.token_map = encrypted_map
            else:
                session.add(PiiVault(case_id=case_id, token_map=encrypted_map))
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to upsert PiiVault for case %s: %s", case_id, exc)
