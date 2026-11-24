"""Agent 6 — Policy Retriever.

Hybrid retrieval: pgvector cosine similarity + Postgres tsvector full-text
search, fused with Reciprocal Rank Fusion (RRF) in a single SQL query.
No external BM25 library — Postgres native FTS only.

Input:  ExtractedClaim  (redacted)
Output: list[PolicyClause]  — top-k clauses with RRF scores
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from app.models import AgentResult, CaseContext, ExtractedClaim, PolicyClause, SourceRef
from db import AsyncSessionFactory
from services.tracing import trace
from services.watsonx import get_client

logger = logging.getLogger(__name__)

# ── Retrieval hyper-parameters ────────────────────────────────────────────────

_K_ANN = 20      # candidates from vector search
_K_FTS = 20      # candidates from full-text search
_TOP_K = 5       # final results after RRF fusion
_RRF_K = 60      # RRF constant (standard default)

# ── SQL query ────────────────────────────────────────────────────────────────

_HYBRID_SQL = """
WITH
  vector_hits AS (
    SELECT
      id,
      (1 - (embedding <=> CAST(:query_vec AS vector))) AS vec_score,
      RANK() OVER (ORDER BY embedding <=> CAST(:query_vec AS vector)) AS rk
    FROM chunks
    ORDER BY embedding <=> CAST(:query_vec AS vector)
    LIMIT :k_ann
  ),
  fts_hits AS (
    SELECT
      id,
      ts_rank_cd(ts_text, query) AS fts_score,
      RANK() OVER (ORDER BY ts_rank_cd(ts_text, query) DESC) AS rk
    FROM chunks, plainto_tsquery('english', :query_text) query
    WHERE ts_text @@ query
    ORDER BY fts_score DESC
    LIMIT :k_fts
  ),
  rrf AS (
    SELECT
      COALESCE(v.id, f.id) AS id,
      (1.0 / (:rrf_k + COALESCE(v.rk, :k_ann + 1))) +
      (1.0 / (:rrf_k + COALESCE(f.rk, :k_fts + 1))) AS rrf_score
    FROM vector_hits v
    FULL OUTER JOIN fts_hits f ON v.id = f.id
  )
SELECT
  c.id,
  c.doc_id,
  c.text,
  c.metadata,
  rrf.rrf_score
FROM rrf
JOIN chunks c ON rrf.id = c.id
ORDER BY rrf.rrf_score DESC
LIMIT :top_k
"""


def _build_query_string(claim: ExtractedClaim) -> str:
    """Concatenate the most retrieval-relevant fields into one query string."""
    parts = [claim.denial_reason_text]
    if claim.cpt_codes:
        parts.append("CPT: " + " ".join(claim.cpt_codes))
    if claim.icd10_codes:
        parts.append("ICD-10: " + " ".join(claim.icd10_codes))
    if claim.denial_reason_code:
        parts.append(f"denial code {claim.denial_reason_code}")
    return " ".join(parts)


def _vec_to_pg(vec: list[float]) -> str:
    """Format a float list as a Postgres vector literal string."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


class PolicyRetriever:
    """Agent 6 — hybrid policy retrieval (pgvector + tsvector RRF)."""

    name = "policy_retriever"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: ExtractedClaim,
        ctx: CaseContext,
    ) -> AgentResult[list[PolicyClause]]:
        client = get_client()

        query_text = _build_query_string(input)
        vectors = await client.embed([query_text])
        query_vec = vectors[0]
        vec_literal = _vec_to_pg(query_vec)

        async with AsyncSessionFactory() as session:
            rows = await session.execute(
                sa_text(_HYBRID_SQL),
                {
                    "query_vec": vec_literal,
                    "query_text": query_text,
                    "k_ann": _K_ANN,
                    "k_fts": _K_FTS,
                    "rrf_k": _RRF_K,
                    "top_k": _TOP_K,
                },
            )
            result_rows: list[Any] = list(rows.fetchall())

        clauses: list[PolicyClause] = []
        for row in result_rows:
            chunk_id, doc_id, text, metadata, rrf_score = row
            meta: dict[str, Any] = metadata or {}
            clauses.append(
                PolicyClause(
                    clause_id=str(chunk_id),
                    doc_id=str(doc_id),
                    section=meta.get("section", "Unknown"),
                    text=text,
                    score=float(rrf_score),
                )
            )

        sources = [
            SourceRef(
                clause_id=c.clause_id,
                doc_id=c.doc_id,
                section=c.section,
                text=c.text[:200],
            )
            for c in clauses
        ]

        confidence = (
            sum(c.score for c in clauses) / len(clauses) if clauses else 0.0
        )

        return AgentResult(
            output=clauses,
            confidence=confidence,
            sources=sources,
            trace_id=ctx.trace_id,
            tokens_in=0,   # embedding calls don't expose token counts via this interface
            tokens_out=0,
            latency_ms=0,  # filled in by @trace decorator
        )
