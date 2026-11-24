"""Tests for Agent 5 — Denial Classifier."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.models import CaseContext, DenialCategory, ExtractedClaim

_REDACTED_CLAIM = ExtractedClaim(
    claim_id="CLM-003",
    payer="UnitedHealth",
    plan_type="employer",
    state="NY",
    service_date="2024-06-01",
    denial_date="2024-06-15",
    cpt_codes=["27447"],
    icd10_codes=["M17.11"],
    billed_amount=Decimal("45000.00"),
    denial_reason_text="Service not medically necessary per utilization review.",
    denial_reason_code="50",
    field_confidence={f: 0.95 for f in [
        "claim_id", "payer", "plan_type", "state",
        "service_date", "denial_date", "cpt_codes",
        "icd10_codes", "billed_amount", "denial_reason_text", "denial_reason_code",
    ]},
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-003", user_id="u-3", trace_id="t-3")


@pytest.fixture(autouse=True)
def _patch_trace(monkeypatch):
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    class _AsyncCtx:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("services.tracing.AsyncSessionFactory", lambda: _AsyncCtx())
    monkeypatch.setattr("services.tracing._get_langfuse", lambda: None)


class TestDenialClassifier:
    async def test_returns_valid_denial_category(self):
        """Classification result must be a valid DenialCategory enum value."""
        from agents.denial_classifier import DenialClassifier

        agent = DenialClassifier()
        response = json.dumps({
            "category": "medical_necessity",
            "rationale": "Payer denied MRI as not medically necessary per utilization review.",
        })

        async def _mock_generate(model_tier, prompt, **kw):
            return response, 10, 15

        with patch("agents.denial_classifier.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _mock_generate
            mock_get.return_value = mock_client

            result = await agent.run(_REDACTED_CLAIM, _make_ctx())

        category, rationale = result.output
        assert isinstance(category, DenialCategory)
        assert category == DenialCategory.MEDICAL_NECESSITY
        assert isinstance(rationale, str)
        assert len(rationale) > 0

    async def test_all_categories_accepted(self):
        """Every DenialCategory value must be parseable from model output."""
        from agents.denial_classifier import DenialClassifier

        agent = DenialClassifier()

        for cat in DenialCategory:
            response = json.dumps({"category": cat.value, "rationale": "test rationale"})

            def _make_generate(r: str):
                async def _gen(model_tier: str, prompt: str, **kw: Any) -> Any:
                    return r, 1, 1
                return _gen

            with patch("agents.denial_classifier.get_client") as mock_get:
                mock_client = MagicMock()
                mock_client.generate = _make_generate(response)
                mock_get.return_value = mock_client

                result = await agent.run(_REDACTED_CLAIM, _make_ctx())

            assert result.output[0] == cat

    async def test_raises_on_invalid_category(self):
        """Unknown category value must raise ValueError."""
        from agents.denial_classifier import DenialClassifier

        agent = DenialClassifier()
        response = json.dumps({"category": "not_a_real_category", "rationale": "oops"})

        async def _mock_generate(model_tier, prompt, **kw):
            return response, 1, 1

        with patch("agents.denial_classifier.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _mock_generate
            mock_get.return_value = mock_client

            with pytest.raises(ValueError, match="could not parse"):
                await agent.run(_REDACTED_CLAIM, _make_ctx())

    async def test_strips_markdown_fences(self):
        """Model output wrapped in ```json fences should still parse."""
        from agents.denial_classifier import DenialClassifier

        agent = DenialClassifier()
        inner = json.dumps({"category": "prior_authorization", "rationale": "Pre-auth missing."})
        fenced = f"```json\n{inner}\n```"

        async def _mock_generate(model_tier, prompt, **kw):
            return fenced, 1, 1

        with patch("agents.denial_classifier.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _mock_generate
            mock_get.return_value = mock_client

            result = await agent.run(_REDACTED_CLAIM, _make_ctx())

        assert result.output[0] == DenialCategory.PRIOR_AUTH

    async def test_uses_routing_model_tier(self):
        """Classifier must use the 'routing' model tier (Granite small)."""
        from agents.denial_classifier import DenialClassifier

        agent = DenialClassifier()
        used_tiers: list[str] = []

        async def _capture(model_tier, prompt, **kw):
            used_tiers.append(model_tier)
            return json.dumps({"category": "administrative", "rationale": "Missing info."}), 1, 1

        with patch("agents.denial_classifier.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _capture
            mock_get.return_value = mock_client

            await agent.run(_REDACTED_CLAIM, _make_ctx())

        assert used_tiers == ["routing"]
