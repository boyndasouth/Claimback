"""Agent 7 — Code Validator.

Fully deterministic — no LLM call.  Validates CPT/ICD-10 combinations in an
ExtractedClaim against three rule types loaded from
``data/fixtures/cpt_icd10.json``:

  1. unknown_code        — code not in the known CPT or ICD-10 lists
  2. invalid_pair        — (cpt, icd10) combination not in valid_pairs table
  3. diagnosis_not_supporting — a CPT code has *no* valid ICD-10 pairing at all
                               among the claim's ICD-10 codes

``missing_modifier`` and ``unbundling`` are reserved for future rule extensions
(no fixture data yet).

Input:  ExtractedClaim
Output: list[CodeIssue]  — empty list means no coding issues detected
"""
from __future__ import annotations

import json
import logging
import pathlib

from app.models import AgentResult, CaseContext, CodeIssue, ExtractedClaim, SourceRef
from services.tracing import trace

logger = logging.getLogger(__name__)

# ── Fixture loading (once at import time) ─────────────────────────────────────

_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "data" / "fixtures" / "cpt_icd10.json"
)

_FIXTURE_DOC_ID = "cpt_icd10_fixture"


def _load_fixture() -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """Return (cpt_set, icd10_set, valid_pairs_set)."""
    data = json.loads(_FIXTURE_PATH.read_text())
    cpt_set: set[str] = set(data.get("cpt_codes", []))
    icd10_set: set[str] = set(data.get("icd10_codes", []))
    pairs: set[tuple[str, str]] = {
        (pair[0], pair[1]) for pair in data.get("valid_pairs", [])
    }
    return cpt_set, icd10_set, pairs


_CPT_CODES, _ICD10_CODES, _VALID_PAIRS = _load_fixture()

# Pre-compute index: cpt_code → set of valid icd10 codes (for quick lookup)
_CPT_TO_VALID_ICD10: dict[str, set[str]] = {}
for _cpt, _icd in _VALID_PAIRS:
    _CPT_TO_VALID_ICD10.setdefault(_cpt, set()).add(_icd)


def _rule_source(clause_id: str, section: str, text: str) -> SourceRef:
    return SourceRef(
        clause_id=clause_id,
        doc_id=_FIXTURE_DOC_ID,
        section=section,
        text=text,
    )


# ── Validation rules ──────────────────────────────────────────────────────────


def _check_unknown_codes(
    cpt_codes: list[str],
    icd10_codes: list[str],
) -> list[CodeIssue]:
    issues: list[CodeIssue] = []
    for cpt in cpt_codes:
        if cpt not in _CPT_CODES:
            issues.append(
                CodeIssue(
                    issue_type="unknown_code",
                    severity="high",
                    cpt_code=cpt,
                    icd10_code=None,
                    message=f"CPT code {cpt!r} is not in the recognised code list.",
                    source=_rule_source(
                        clause_id=f"cpt_unknown:{cpt}",
                        section="CPT Codes",
                        text=f"Code {cpt} not found in CPT reference table.",
                    ),
                )
            )
    for icd in icd10_codes:
        if icd not in _ICD10_CODES:
            issues.append(
                CodeIssue(
                    issue_type="unknown_code",
                    severity="high",
                    cpt_code=None,
                    icd10_code=icd,
                    message=f"ICD-10 code {icd!r} is not in the recognised code list.",
                    source=_rule_source(
                        clause_id=f"icd10_unknown:{icd}",
                        section="ICD-10 Codes",
                        text=f"Code {icd} not found in ICD-10 reference table.",
                    ),
                )
            )
    return issues


def _check_invalid_pairs(
    cpt_codes: list[str],
    icd10_codes: list[str],
) -> list[CodeIssue]:
    """Flag (cpt, icd10) combinations that are not in the valid_pairs table."""
    issues: list[CodeIssue] = []
    for cpt in cpt_codes:
        # Only check known codes — unknown codes already flagged above
        if cpt not in _CPT_CODES:
            continue
        valid_for_cpt = _CPT_TO_VALID_ICD10.get(cpt, set())
        if not valid_for_cpt:
            # CPT has no entries in valid_pairs at all; skip pair check here
            # (covered by diagnosis_not_supporting rule instead)
            continue
        for icd in icd10_codes:
            if icd not in _ICD10_CODES:
                continue
            if (cpt, icd) not in _VALID_PAIRS:
                issues.append(
                    CodeIssue(
                        issue_type="invalid_pair",
                        severity="medium",
                        cpt_code=cpt,
                        icd10_code=icd,
                        message=(
                            f"CPT {cpt} and ICD-10 {icd} are not a recognised valid pair. "
                            "Documentation of medical necessity may be required."
                        ),
                        source=_rule_source(
                            clause_id=f"pair:{cpt}:{icd}",
                            section="Valid Pairs",
                            text=f"Pair ({cpt}, {icd}) not in valid_pairs reference table.",
                        ),
                    )
                )
    return issues


def _check_diagnosis_not_supporting(
    cpt_codes: list[str],
    icd10_codes: list[str],
) -> list[CodeIssue]:
    """Flag CPT codes for which none of the claim's ICD-10 codes are valid pairs."""
    issues: list[CodeIssue] = []
    icd_set = set(icd10_codes)
    for cpt in cpt_codes:
        if cpt not in _CPT_CODES:
            continue  # already flagged as unknown
        valid_for_cpt = _CPT_TO_VALID_ICD10.get(cpt)
        if valid_for_cpt is None:
            # CPT not in valid_pairs at all — no diagnosis evidence
            issues.append(
                CodeIssue(
                    issue_type="diagnosis_not_supporting",
                    severity="medium",
                    cpt_code=cpt,
                    icd10_code=None,
                    message=(
                        f"CPT {cpt} has no valid ICD-10 pairings in the reference table. "
                        "No diagnosis supports this procedure."
                    ),
                    source=_rule_source(
                        clause_id=f"no_pairs:{cpt}",
                        section="Valid Pairs",
                        text=f"CPT {cpt} has no entries in the valid_pairs reference table.",
                    ),
                )
            )
        elif not icd_set.intersection(valid_for_cpt):
            # CPT has valid pairings but none of the claim's ICD-10 codes qualify
            issues.append(
                CodeIssue(
                    issue_type="diagnosis_not_supporting",
                    severity="medium",
                    cpt_code=cpt,
                    icd10_code=None,
                    message=(
                        f"None of the claim's diagnosis codes support CPT {cpt}. "
                        f"Valid diagnoses include: {', '.join(sorted(valid_for_cpt)[:5])}."
                    ),
                    source=_rule_source(
                        clause_id=f"no_matching_pair:{cpt}",
                        section="Valid Pairs",
                        text=(
                            f"CPT {cpt}: none of the claim ICD-10 codes match "
                            "valid_pairs reference table."
                        ),
                    ),
                )
            )
    return issues


# ── Agent ─────────────────────────────────────────────────────────────────────


class CodeValidator:
    """Agent 7 — deterministic CPT/ICD-10 code validation."""

    name = "code_validator"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: ExtractedClaim,
        ctx: CaseContext,
    ) -> AgentResult[list[CodeIssue]]:
        issues: list[CodeIssue] = []
        issues.extend(_check_unknown_codes(input.cpt_codes, input.icd10_codes))
        issues.extend(_check_invalid_pairs(input.cpt_codes, input.icd10_codes))
        issues.extend(_check_diagnosis_not_supporting(input.cpt_codes, input.icd10_codes))

        # De-duplicate sources for the AgentResult.sources list
        seen_clause_ids: set[str] = set()
        sources: list[SourceRef] = []
        for issue in issues:
            if issue.source.clause_id not in seen_clause_ids:
                seen_clause_ids.add(issue.source.clause_id)
                sources.append(issue.source)

        return AgentResult(
            output=issues,
            confidence=1.0,
            sources=sources,
            trace_id=ctx.trace_id,
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,  # filled in by @trace
        )
