"""Appeal-related Pydantic models (§6)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from .base import FlaggedSpan, SourceRef
from .claim import CodeIssue, DenialCategory, ExtractedClaim, PolicyClause


class AppealPlan(BaseModel):
    internal_appeal_deadline: date
    external_review_available: bool
    external_review_deadline: date | None
    expedited_eligible: bool
    rule_sources: list[SourceRef]


class AppealDraft(BaseModel):
    body_markdown: str
    citations: dict[str, SourceRef]  # "S1" -> source
    version: int


class GroundingReport(BaseModel):
    passed: bool
    flagged_spans: list[FlaggedSpan]
    groundedness_score: float  # fraction of sentences grounded


class SuccessEstimate(BaseModel):
    probability: float
    top_factors: list[tuple[str, float]]
    explanation: str


# ── Day-5 input / output composites ───────────────────────────────────────────


class EvidenceItem(BaseModel):
    """A single evidence item in the checklist produced by Agent 9."""

    item_id: str
    description: str
    rationale: str
    priority: Literal["high", "medium", "low"]


class EvidenceChecklist(BaseModel):
    """Structured checklist output of Agent 9 (Evidence Builder)."""

    items: list[EvidenceItem]


class EvidenceBuilderInput(BaseModel):
    """Bundled input for Agent 9 so the @trace hash covers all three fields."""

    category: DenialCategory
    clauses: list[PolicyClause]
    issues: list[CodeIssue]


class WriterInput(BaseModel):
    """Full input bundle for Agent 10 (Appeal Writer) on first pass."""

    checklist: EvidenceChecklist
    claim: ExtractedClaim
    clauses: list[PolicyClause]
    issues: list[CodeIssue]
    plan: AppealPlan


class WriterRetryInput(BaseModel):
    """Input bundle for Agent 10 on rewrite passes."""

    base: WriterInput
    flagged_spans: list[FlaggedSpan]
    previous_draft: AppealDraft


class CheckerInput(BaseModel):
    """Input for Agent 11 (Groundedness Checker)."""

    draft: AppealDraft
    sources: list[SourceRef]


class SuccessScorerInput(BaseModel):
    """Feature vector input for Agent 12 (Success Scorer)."""

    category: DenialCategory
    billed_amount: float
    state: str
    plan_type: str
    code_issues_count: int
    expedited_eligible: bool
