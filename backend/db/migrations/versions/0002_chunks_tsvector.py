"""Add tsvector column and GIN index to chunks table for full-text search.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add a generated stored tsvector column derived from the text column.
    # GENERATED ALWAYS AS ... STORED keeps the column in sync automatically —
    # no trigger or application code needed.  Requires Postgres >= 12.
    op.execute(
        """
        ALTER TABLE chunks
            ADD COLUMN ts_text tsvector
            GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
        """
    )
    # GIN index enables fast @@ queries (§16 hybrid retrieval)
    op.execute(
        "CREATE INDEX ix_chunks_ts_text ON chunks USING GIN (ts_text)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_ts_text")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS ts_text")
