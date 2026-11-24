"""Tests for Agent 7 — Code Validator."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import CaseContext, CodeIssue, ExtractedClaim


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-007", user_id="u-7", trace_id="t-7")


def _claim(**overrides: object) -> ExtractedClaim:
    base: dict[str, object] = dict(
        claim_id="CLM-007",
        payer="Aetna",
        plan_type="employer",
        state="CA",
        service_date="2024-03-01",
        denial_date="2024-03-15",
        cpt_codes=["99214"],
        icd10_codes=["E11.9"],
        billed_amount=Decimal("450.00"),
        denial_reason_text="Service not medically necessary.",
        denial_reason_code="CO-50",
        field_confidence={},
    )
    base.update(overrides)
    return ExtractedClaim(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _patch_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    class _AsyncCtx:
        async def __aenter__(self) -> MagicMock:
            return mock_session

        async def __aexit__(self, *a: object) -> bool:
            return False

    monkeypatch.setattr("services.tracing.AsyncSessionFactory", lambda: _AsyncCtx())
    monkeypatch.setattr("services.tracing._get_langfuse", lambda: None)


class TestCodeValidatorCleanClaim:
    async def test_clean_claim_returns_empty_list(self) -> None:
        """A claim with a known valid (CPT, ICD-10) pair returns no issues."""
        from agents.code_validator import CodeValidator

        # (99214, E11.9) is in the valid_pairs fixture
        agent = CodeValidator()
        result = await agent.run(_claim(cpt_codes=["99214"], icd10_codes=["E11.9"]), _make_ctx())

        assert result.output == []
        assert result.confidence == 1.0
        assert result.tokens_in == 0
        assert result.tokens_out == 0

    async def test_multiple_valid_pairs_no_issues(self) -> None:
        """Multiple codes all with valid pairings should produce no issues."""
        from agents.code_validator import CodeValidator

        agent = CodeValidator()
        # (85025, E11.9) and (80053, I10) are both in valid_pairs
        result = await agent.run(
            _claim(cpt_codes=["85025", "80053"], icd10_codes=["E11.9", "I10"]),
            _make_ctx(),
        )
        # Neither (85025, I10) nor (80053, E11.9) is explicitly listed,
        # but each CPT has at least one matching ICD in the claim
        # so diagnosis_not_supporting should NOT fire
        assert all(i.issue_type != "diagnosis_not_supporting" for i in result.output)


class TestCodeValidatorUnknownCode:
    async def test_unknown_cpt_flagged_high_severity(self) -> None:
        """An unrecognised CPT code must produce an 'unknown_code' issue with severity 'high'."""
        from agents.code_validator import CodeValidator

        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["BOGUS99"], icd10_codes=["E11.9"]),
            _make_ctx(),
        )

        unknown_cpt = [i for i in result.output if i.issue_type == "unknown_code" and i.cpt_code == "BOGUS99"]
        assert len(unknown_cpt) == 1
        assert unknown_cpt[0].severity == "high"
        assert unknown_cpt[0].icd10_code is None

    async def test_unknown_icd10_flagged_high_severity(self) -> None:
        """An unrecognised ICD-10 code must produce an 'unknown_code' issue with severity 'high'."""
        from agents.code_validator import CodeValidator

        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["99214"], icd10_codes=["ZZZ.99"]),
            _make_ctx(),
        )

        unknown_icd = [i for i in result.output if i.issue_type == "unknown_code" and i.icd10_code == "ZZZ.99"]
        assert len(unknown_icd) == 1
        assert unknown_icd[0].severity == "high"
        assert unknown_icd[0].cpt_code is None

    async def test_multiple_unknown_codes_all_flagged(self) -> None:
        """Every unknown code must generate its own 'unknown_code' issue."""
        from agents.code_validator import CodeValidator

        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["BAD1", "BAD2"], icd10_codes=["ZZZ.1"]),
            _make_ctx(),
        )

        unknown = [i for i in result.output if i.issue_type == "unknown_code"]
        codes_flagged = {i.cpt_code for i in unknown if i.cpt_code} | {i.icd10_code for i in unknown if i.icd10_code}
        assert "BAD1" in codes_flagged
        assert "BAD2" in codes_flagged
        assert "ZZZ.1" in codes_flagged


class TestCodeValidatorInvalidPair:
    async def test_known_codes_bad_pair_flagged_medium(self) -> None:
        """A (CPT, ICD-10) pair not in valid_pairs must produce 'invalid_pair' medium severity."""
        from agents.code_validator import CodeValidator

        # 70553 (brain MRI) paired with E11.9 (diabetes) — not a valid pair in fixture
        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["70553"], icd10_codes=["E11.9"]),
            _make_ctx(),
        )

        pair_issues = [i for i in result.output if i.issue_type == "invalid_pair"]
        assert len(pair_issues) >= 1
        assert all(i.severity == "medium" for i in pair_issues)
        assert pair_issues[0].cpt_code == "70553"
        assert pair_issues[0].icd10_code == "E11.9"

    async def test_valid_pair_not_flagged(self) -> None:
        """A known valid pair must NOT produce an 'invalid_pair' issue."""
        from agents.code_validator import CodeValidator

        # (70553, G35) is explicitly in valid_pairs
        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["70553"], icd10_codes=["G35"]),
            _make_ctx(),
        )

        pair_issues = [i for i in result.output if i.issue_type == "invalid_pair"]
        assert pair_issues == []


class TestCodeValidatorDiagnosisNotSupporting:
    async def test_cpt_with_no_matching_icd_flagged(self) -> None:
        """CPT with valid_pairs but none matching the claim's ICD-10 → 'diagnosis_not_supporting'."""
        from agents.code_validator import CodeValidator

        # 76817 (OB ultrasound) has valid pairs with obstetric ICD codes;
        # pairing with E11.9 (diabetes) means no claim ICD supports it
        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["76817"], icd10_codes=["E11.9"]),
            _make_ctx(),
        )

        diag_issues = [i for i in result.output if i.issue_type == "diagnosis_not_supporting"]
        assert len(diag_issues) == 1
        assert diag_issues[0].cpt_code == "76817"
        assert diag_issues[0].severity == "medium"

    async def test_cpt_fully_supported_no_diag_issue(self) -> None:
        """CPT code supported by at least one claim ICD must NOT trigger diagnosis_not_supporting."""
        from agents.code_validator import CodeValidator

        # (93306, I50.9) is in valid_pairs
        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["93306"], icd10_codes=["I50.9"]),
            _make_ctx(),
        )

        diag_issues = [i for i in result.output if i.issue_type == "diagnosis_not_supporting"]
        assert diag_issues == []


class TestCodeValidatorSourceRef:
    async def test_source_ref_points_to_fixture(self) -> None:
        """Every CodeIssue.source must reference the cpt_icd10_fixture doc."""
        from agents.code_validator import CodeValidator

        agent = CodeValidator()
        result = await agent.run(
            _claim(cpt_codes=["BOGUS"], icd10_codes=["E11.9"]),
            _make_ctx(),
        )

        for issue in result.output:
            assert issue.source.doc_id == "cpt_icd10_fixture"
            assert isinstance(issue.source.clause_id, str)
            assert len(issue.source.clause_id) > 0

    async def test_sources_deduplicated_in_agent_result(self) -> None:
        """AgentResult.sources must not contain duplicate clause_ids."""
        from agents.code_validator import CodeValidator

        agent = CodeValidator()
        # Multiple bad codes → multiple issues → sources should be deduplicated
        result = await agent.run(
            _claim(cpt_codes=["BAD1", "BAD2"], icd10_codes=["ZZZ.1", "ZZZ.2"]),
            _make_ctx(),
        )

        clause_ids = [s.clause_id for s in result.sources]
        assert len(clause_ids) == len(set(clause_ids)), "Duplicate source clause_ids found"


class TestCodeIssueModel:
    def test_code_issue_all_issue_types_valid(self) -> None:
        """All five issue_type literals must be accepted by the CodeIssue model."""
        from app.models import CodeIssue, SourceRef

        for itype in ("invalid_pair", "unknown_code", "missing_modifier", "unbundling", "diagnosis_not_supporting"):
            issue = CodeIssue(
                issue_type=itype,  # type: ignore[arg-type]
                severity="medium",
                cpt_code="99214",
                icd10_code="E11.9",
                message="Test.",
                source=SourceRef(clause_id="x", doc_id="y", section="z", text="t"),
            )
            assert issue.issue_type == itype
