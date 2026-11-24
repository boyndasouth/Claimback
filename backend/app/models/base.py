"""Shared base types used across all models."""
from __future__ import annotations

from pydantic import BaseModel


class SourceRef(BaseModel):
    """A grounded reference to a source document clause."""

    clause_id: str
    doc_id: str
    section: str
    text: str


class FlaggedSpan(BaseModel):
    """A span of text flagged by the Groundedness Checker."""

    span: str
    reason: str


class CaseContext(BaseModel):
    """Ambient context passed to every agent run."""

    case_id: str
    user_id: str
    trace_id: str
