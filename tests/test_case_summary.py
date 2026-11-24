"""Tests for services.case_summary agent-output parsing."""
from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import AppealPlan, SuccessEstimate  # noqa: E402
from services.case_summary import _canonical, _parse_agent_output  # noqa: E402


def test_canonical_accepts_both_name_styles():
    assert _canonical("RegulationDeadline") == "regulation_deadline"
    assert _canonical("regulation_deadline") == "regulation_deadline"


def test_parses_snake_case_rows_as_stored_by_tracing():
    plan = _parse_agent_output(
        "regulation_deadline",
        {"output": {
            "internal_appeal_deadline": "2027-01-21",
            "external_review_available": True,
            "external_review_deadline": "2027-02-20",
            "expedited_eligible": False,
            "rule_sources": [],
        }},
    )
    assert isinstance(plan, AppealPlan)

    est = _parse_agent_output(
        "success_scorer",
        {"output": {"probability": 0.5, "top_factors": [], "explanation": "x"}},
    )
    assert isinstance(est, SuccessEstimate)


def test_classifier_tuple_output_gives_rationale():
    out = _parse_agent_output(
        "denial_classifier", {"output": ["medical_necessity", "Lacks documentation."]}
    )
    assert out == "Lacks documentation."
