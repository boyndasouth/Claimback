"""Initial schema: pgvector extension + all 7 tables.

Revision ID: 0001
Revises:
Create Date: 2026-11-22

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("success_prob", sa.Float, nullable=True),
    )
    op.create_index("ix_cases_user_id", "cases", ["user_id"])
    op.create_index("ix_cases_status", "cases", ["status"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("storage_url", sa.Text, nullable=False),
        sa.Column("extracted_json", postgresql.JSONB, nullable=True),
    )
    op.create_index("ix_documents_case_id", "documents", ["case_id"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "doc_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "embedding",
            sa.Text,  # placeholder; actual vector(768) set via raw DDL below
            nullable=False,
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=False),
    )
    # Replace placeholder with real vector column
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(768) USING embedding::vector")
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output", postgresql.JSONB, nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=False),
        sa.Column("tokens_out", sa.Integer, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("trace_id", sa.String(128), nullable=False),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"])
    # Idempotency index (§16)
    op.create_index(
        "uq_agent_runs_idempotency",
        "agent_runs",
        ["case_id", "agent", "input_hash"],
        unique=True,
    )

    op.create_table(
        "drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("grounding_report", postgresql.JSONB, nullable=True),
        sa.Column("approved_by", sa.String(256), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_drafts_case_id", "drafts", ["case_id"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("sent", sa.Boolean, nullable=False, default=False),
    )
    op.create_index("ix_reminders_case_id", "reminders", ["case_id"])

    op.create_table(
        "pii_vault",
        sa.Column(
            "case_id",
            sa.String(36),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("token_map", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pii_vault")
    op.drop_table("reminders")
    op.drop_table("drafts")
    op.drop_table("agent_runs")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("cases")
    op.execute("DROP EXTENSION IF EXISTS vector")
