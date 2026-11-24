"""Claim-related Pydantic models (§6)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from .base import SourceRef


class DenialCategory(StrEnum):
    MEDICAL_NECESSITY = "medical_necessity"
    PRIOR_AUTH = "prior_authorization"
    CODING_ERROR = "coding_error"
    OUT_OF_NETWORK = "out_of_network"
    NOT_COVERED = "not_covered_benefit"
    ADMINISTRATIVE = "administrative"


class ExtractedClaim(BaseModel):
    claim_id: str
    payer: str
    plan_type: Literal["employer", "marketplace", "medicaid", "medicare_advantage"]
    state: str  # 2-letter
    service_date: date
    denial_date: date
    cpt_codes: list[str]
    icd10_codes: list[str]
    billed_amount: Decimal
    denial_reason_text: str
    denial_reason_code: str | None  # CARC/RARC if present
    field_confidence: dict[str, float]


class PolicyClause(BaseModel):
    clause_id: str
    doc_id: str
    section: str
    text: str
    score: float


class CodeIssue(BaseModel):
    issue_type: Literal[
        "invalid_pair",
        "unknown_code",
        "missing_modifier",
        "unbundling",
        "diagnosis_not_supporting",
    ]
    severity: Literal["low", "medium", "high"]
    cpt_code: str | None
    icd10_code: str | None
    message: str
    source: SourceRef
