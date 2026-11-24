"""Unit tests for Agent 12 — Success Scorer."""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.models import (
    AgentResult,
    CaseContext,
    DenialCategory,
    SuccessEstimate,
    SuccessScorerInput,
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="c1", user_id="u1", trace_id="t1")


def _make_input() -> SuccessScorerInput:
    return SuccessScorerInput(
        category=DenialCategory.MEDICAL_NECESSITY,
        billed_amount=450.0,
        state="CA",
        plan_type="employer",
        code_issues_count=0,
        expedited_eligible=False,
    )


class TestSuccessScorer:
    def test_fallback_probability_when_no_model(self) -> None:
        """When no pkl file exists, scorer returns 0.5 with an explanation."""
        mock_explanation = "This appeal has a moderate probability of success."
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=(mock_explanation, 50, 25))

        with patch("agents.success_scorer.get_client", return_value=mock_client):
            with patch("agents.success_scorer._booster", None):
                from agents.success_scorer import SuccessScorer
                agent = SuccessScorer()
                inp = _make_input()
                ctx = _make_ctx()

                import asyncio
                result: AgentResult[SuccessEstimate] = asyncio.run(agent.run(inp, ctx))

        assert 0.0 <= result.output.probability <= 1.0
        assert result.output.probability == 0.5
        assert result.output.explanation != ""

    def test_probability_in_range_with_mocked_booster(self) -> None:
        """When a booster is present, probability comes from predict_proba."""
        import numpy as np

        mock_booster = MagicMock()
        mock_booster.predict_proba = MagicMock(return_value=np.array([[0.3, 0.7]]))
        mock_booster.feature_importances_ = [0.1] * 62  # 62 features

        mock_explanation = "High probability due to coding error category."
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=(mock_explanation, 50, 25))

        with patch("agents.success_scorer.get_client", return_value=mock_client):
            with patch("agents.success_scorer._booster", mock_booster):
                from agents.success_scorer import SuccessScorer
                agent = SuccessScorer()
                inp = _make_input()
                ctx = _make_ctx()

                import asyncio
                result: AgentResult[SuccessEstimate] = asyncio.run(agent.run(inp, ctx))

        assert abs(result.output.probability - 0.7) < 0.01
        assert result.output.explanation == mock_explanation
        assert 0 <= len(result.output.top_factors) <= 3
