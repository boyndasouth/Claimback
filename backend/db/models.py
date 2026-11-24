"""SQLAlchemy ORM models for all §6.1 tables."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    documents: Mapped[list[Document]] = relationship(back_populates="case")
    agent_runs: Mapped[list[AgentRun]] = relationship(back_populates="case")
    drafts: Mapped[list[Draft]] = relationship(back_populates="case")
    reminders: Mapped[list[Reminder]] = relationship(back_populates="case")
    pii_vault: Mapped[PiiVault | None] = relationship(back_populates="case")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # denial/eob/policy
    storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    case: Mapped[Case] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    # Read-only: populated by Postgres GENERATED ALWAYS AS tsvector (migration 0002)
    ts_text: Mapped[str | None] = mapped_column(
        Text,
        Computed("to_tsvector('english', text)", persisted=True),
        nullable=True,
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)

    case: Mapped[Case] = relationship(back_populates="agent_runs")

    # Idempotency: (case_id, agent, input_hash) must be unique (§16)
    __table_args__ = (
        {"extend_existing": False},
    )


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    grounding_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    case: Mapped[Case] = relationship(back_populates="drafts")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    case: Mapped[Case] = relationship(back_populates="reminders")


class PiiVault(Base):
    __tablename__ = "pii_vault"

    case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Encrypted Fernet blob; never stored plain
    token_map: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[Case] = relationship(back_populates="pii_vault")
