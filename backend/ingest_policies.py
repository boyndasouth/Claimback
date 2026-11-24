#!/usr/bin/env python3
"""Policy PDF ingest script — chunks policy documents and stores embeddings in pgvector.

Usage::

    python backend/ingest_policies.py

Or via Makefile::

    make ingest

Idempotency: a chunk is skipped when a row with the same ``content_hash``
already exists in the ``chunks.metadata`` JSONB field.  Re-running the script
after policy files change will only insert new/changed chunks.

Expected input: ``data/generated/policies/`` containing the 8 synthetic policy
PDFs (or ``.html`` files when weasyprint is unavailable).  Run ``make data``
first to generate them.

Environment variables required (same as the backend)::

    DATABASE_URL          — asyncpg-compatible Postgres DSN
    WATSONX_API_KEY       — watsonx.ai API key
    WATSONX_PROJECT_ID    — watsonx.ai project ID
    WATSONX_URL           — optional; defaults to us-south endpoint
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import pathlib
import re
import sys
import uuid
from typing import Any

# ── Path setup ───────────────────────────────────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env", override=False)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ingest")

# ── Constants ─────────────────────────────────────────────────────────────────

_POLICIES_DIR = _REPO_ROOT / "data" / "generated" / "policies"

# Chunking parameters (plan §3)
# granite-embedding-278m has a hard 512-token limit; policy text (numbers,
# section refs, punctuation) runs ~1.7 tokens/word, so 300 words overflowed.
_CHUNK_WORDS = 200          # target words per chunk  (≈ 350 tokens)
_OVERLAP_WORDS = 30         # overlap words           (≈ 50 tokens)
_EMBED_BATCH = 16           # texts per embed() call

# Sentinel doc_id for policy documents not linked to a case
_POLICY_KIND = "policy"


# ── Chunking ──────────────────────────────────────────────────────────────────


def _extract_section(text_before: str) -> str:
    """Return the last heading-like line found before this chunk position."""
    # Match lines that look like headings: ALL CAPS, Title Case with number, or ## style
    headings = re.findall(
        r"(?m)^(?:#{1,3}\s+.+|[A-Z][A-Z0-9 ,&]+(?:\n|$)|\d+\.\s+[A-Z].{3,})",
        text_before,
    )
    return headings[-1].strip() if headings else "General"


_HEADING_LINE = re.compile(r"^#{1,4}\s+(.+)$")
_SKIP_SECTIONS = {"table of contents"}


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown-ish text (``## Heading`` lines) into (section, body) pairs."""
    sections: list[tuple[str, list[str]]] = [("General", [])]
    for line in text.splitlines():
        m = _HEADING_LINE.match(line.strip())
        if m:
            sections.append((m.group(1).strip(), []))
        elif line.strip():
            sections[-1][1].append(line.strip())
    return [
        (title, " ".join(lines))
        for title, lines in sections
        if lines and title.lower() not in _SKIP_SECTIONS
    ]


def _chunk_text(text: str) -> list[dict[str, Any]]:
    """Chunk by section heading, then window long sections.

    Each chunk is prefixed with its section title, so retrieval can match on
    "Diagnostic Imaging" and citations can name the exact section.
    Returns dicts with keys ``text``, ``section``, ``page`` (0 — page tracking
    needs PDF-level parsing).
    """
    chunks: list[dict[str, Any]] = []
    for section, body in _split_sections(text):
        words = body.split()
        start = 0
        while start < len(words):
            end = min(start + _CHUNK_WORDS, len(words))
            chunk_body = " ".join(words[start:end])
            chunks.append({"text": f"{section}\n{chunk_body}", "section": section, "page": 0})
            if end == len(words):
                break
            start = end - _OVERLAP_WORDS
    return chunks


def _content_hash(text: str, doc_id: str) -> str:
    # Scoped per document: insurers share boilerplate text, and a global hash
    # silently dropped those sections from every policy after the first.
    return hashlib.sha256(f"{doc_id}|{text}".encode()).hexdigest()


# ── Text extraction ───────────────────────────────────────────────────────────


def _extract_text(path: pathlib.Path) -> str:
    """Extract plain text from a PDF or HTML policy file."""
    if path.suffix.lower() == ".pdf":
        try:
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            result = converter.convert(str(path))
            markdown: str = result.document.export_to_markdown()
            return markdown
        except Exception as exc:
            logger.warning("Docling failed for %s (%s) — falling back to raw read", path.name, exc)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".html", ".htm"):
        return _html_to_text(raw)
    return raw


def _html_to_text(html: str) -> str:
    """Strip markup/CSS; keep h1–h4 as ``## Heading`` lines for sectioning."""
    from bs4 import BeautifulSoup  # noqa: PLC0415

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "script", "head"]):
        tag.decompose()
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        h.replace_with(f"\n## {h.get_text(' ', strip=True)}\n")
    return soup.get_text("\n")


# ── Database helpers ──────────────────────────────────────────────────────────


async def _existing_hashes(session: Any) -> set[str]:
    """Return all content_hashes already stored in chunks.metadata."""
    from sqlalchemy import text as sa_text  # noqa: PLC0415

    rows = await session.execute(
        sa_text("SELECT metadata->>'content_hash' FROM chunks WHERE metadata ? 'content_hash'")
    )
    return {r[0] for r in rows if r[0]}


async def _ensure_policy_document(session: Any, doc_id: str, storage_url: str) -> None:
    """Upsert a stub Document row for a policy file.

    documents.case_id is a NOT NULL foreign key, so policy documents hang off a
    reserved "system" case (all-zero UUID), created here if it doesn't exist.
    """
    from sqlalchemy import text as sa_text  # noqa: PLC0415

    await session.execute(
        sa_text(
            """
            INSERT INTO cases (id, status, user_id)
            VALUES ('00000000-0000-0000-0000-000000000000', 'closed', 'system')
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    await session.execute(
        sa_text(
            """
            INSERT INTO documents (id, case_id, kind, storage_url)
            VALUES (:id, '00000000-0000-0000-0000-000000000000', :kind, :url)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": doc_id, "kind": _POLICY_KIND, "url": storage_url},
    )


async def _insert_chunks(
    session: Any,
    doc_id: str,
    chunk_rows: list[dict[str, Any]],
) -> None:
    """Bulk-insert chunk rows (text + embedding + metadata)."""
    from sqlalchemy import text as sa_text  # noqa: PLC0415

    for row in chunk_rows:
        # pgvector expects the embedding as a Python list; SQLAlchemy casts it
        vec_str = "[" + ",".join(f"{v:.8f}" for v in row["embedding"]) + "]"
        await session.execute(
            sa_text(
                """
                INSERT INTO chunks (id, doc_id, text, embedding, metadata)
                VALUES (
                    :id, :doc_id, :text,
                    CAST(:embedding AS vector),
                    CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "doc_id": doc_id,
                "text": row["text"],
                "embedding": vec_str,
                "metadata": __import__("json").dumps(row["metadata"]),
            },
        )


# ── Main ingest logic ─────────────────────────────────────────────────────────


async def _ingest_all() -> None:
    from db import AsyncSessionFactory  # noqa: PLC0415
    from services.watsonx import get_client  # noqa: PLC0415

    client = get_client()

    # Discover policy files (.pdf preferred, .html as fallback)
    policy_files: list[pathlib.Path] = []
    for ext in ("*.pdf", "*.html"):
        policy_files.extend(sorted(_POLICIES_DIR.glob(ext)))
    # Deduplicate: prefer .pdf over .html for the same stem
    seen_stems: set[str] = set()
    deduped: list[pathlib.Path] = []
    for p in policy_files:
        if p.stem not in seen_stems:
            seen_stems.add(p.stem)
            deduped.append(p)
    policy_files = deduped

    if not policy_files:
        logger.warning(
            "No policy files found in %s — run 'make data' first.", _POLICIES_DIR
        )
        return

    total_ingested = 0
    total_skipped = 0

    async with AsyncSessionFactory() as session:
        existing = await _existing_hashes(session)
        logger.info("Found %d existing chunk hashes in DB.", len(existing))

        for policy_path in policy_files:
            logger.info("Processing %s …", policy_path.name)
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(policy_path)))
            storage_url = str(policy_path.relative_to(_REPO_ROOT))

            await _ensure_policy_document(session, doc_id, storage_url)

            raw_text = _extract_text(policy_path)
            raw_chunks = _chunk_text(raw_text)

            # Filter chunks already in DB
            new_chunks = [
                c for c in raw_chunks
                if _content_hash(c["text"], doc_id) not in existing
            ]
            total_skipped += len(raw_chunks) - len(new_chunks)

            if not new_chunks:
                logger.info("  All %d chunks already ingested — skipping.", len(raw_chunks))
                continue

            logger.info(
                "  %d / %d chunks new — embedding in batches of %d …",
                len(new_chunks), len(raw_chunks), _EMBED_BATCH,
            )

            # Embed in batches
            chunk_rows: list[dict[str, Any]] = []
            for i in range(0, len(new_chunks), _EMBED_BATCH):
                batch = new_chunks[i : i + _EMBED_BATCH]
                texts = [c["text"] for c in batch]
                vectors = await client.embed(texts)
                for chunk, vec in zip(batch, vectors, strict=True):
                    h = _content_hash(chunk["text"], doc_id)
                    existing.add(h)  # prevent re-ingest within this run
                    chunk_rows.append({
                        "text": chunk["text"],
                        "embedding": vec,
                        "metadata": {
                            "doc_id": doc_id,
                            "section": chunk["section"],
                            "page": chunk["page"],
                            "content_hash": h,
                            "source_file": policy_path.name,
                        },
                    })

            await _insert_chunks(session, doc_id, chunk_rows)
            await session.commit()
            total_ingested += len(chunk_rows)
            logger.info("  Inserted %d chunks for %s.", len(chunk_rows), policy_path.name)

    print(
        f"\nIngest complete: {len(policy_files)} documents, "
        f"{total_ingested} chunks ingested, "
        f"{total_skipped} chunks skipped (already present)."
    )


def main() -> int:
    # Validate required env vars before attempting DB/watsonx calls
    missing = [v for v in ("DATABASE_URL", "WATSONX_API_KEY", "WATSONX_PROJECT_ID") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    asyncio.run(_ingest_all())
    return 0


if __name__ == "__main__":
    sys.exit(main())
