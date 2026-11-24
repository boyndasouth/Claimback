"""Tests for Agent 4 — PII Redactor.

Includes a critical test asserting that no raw PII from the token map appears
in downstream prompts (SPEC §19 acceptance criterion).
"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.models import CaseContext, ExtractedClaim
from cryptography.fernet import Fernet

# Generate a test Fernet key
_TEST_KEY = Fernet.generate_key().decode()

_PII_CLAIM = ExtractedClaim(
    claim_id="CLM-002",
    payer="BlueCross",
    plan_type="employer",
    state="TX",
    service_date="2024-05-10",
    denial_date="2024-05-25",
    cpt_codes=["99213"],
    icd10_codes=["J06.9"],
    billed_amount=Decimal("350.00"),
    denial_reason_text=(
        "Patient John Smith (SSN 123-45-6789, DOB 01/15/1985) was denied "
        "coverage for visit on 2024-05-10. Contact member at (555) 867-5309 "
        "or john.smith@email.com for further information."
    ),
    denial_reason_code="4",
    field_confidence={f: 0.9 for f in [
        "claim_id", "payer", "plan_type", "state",
        "service_date", "denial_date", "cpt_codes",
        "icd10_codes", "billed_amount", "denial_reason_text", "denial_reason_code",
    ]},
)

_PII_PATTERNS_TO_CHECK = [
    "John Smith",
    "123-45-6789",
    "01/15/1985",
    "(555) 867-5309",
    "john.smith@email.com",
]


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-002", user_id="u-2", trace_id="t-2")


@pytest.fixture(autouse=True)
def _patch_trace_and_vault(monkeypatch):
    """Disable DB persistence and patch env."""
    monkeypatch.setenv("PII_VAULT_KEY", _TEST_KEY)

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    class _AsyncCtx:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("services.tracing.AsyncSessionFactory", lambda: _AsyncCtx())
    monkeypatch.setattr("services.tracing._get_langfuse", lambda: None)


class TestPiiRedactor:
    async def test_known_pii_replaced_by_tokens(self):
        """PII spans in denial_reason_text must be replaced with [TYPE_N] tokens."""
        from agents.pii_redactor import PiiRedactor

        agent = PiiRedactor()

        # NER pass returns empty (regex pass should catch everything)
        async def _mock_generate(model_tier, prompt, **kw):
            return "{}", 5, 5

        with (
            patch("agents.pii_redactor.get_client") as mock_get,
            patch("agents.pii_redactor._upsert_pii_vault", new_callable=AsyncMock),
        ):
            mock_client = MagicMock()
            mock_client.generate = _mock_generate
            mock_get.return_value = mock_client

            result = await agent.run(_PII_CLAIM, _make_ctx())

        redacted_text = result.output.denial_reason_text
        for pii in _PII_PATTERNS_TO_CHECK:
            assert pii not in redacted_text, (
                f"PII '{pii}' still present in redacted denial_reason_text: {redacted_text!r}"
            )

    async def test_token_map_contains_original_pii(self):
        """The token_map passed to _upsert_pii_vault must contain the original PII values."""
        from agents.pii_redactor import PiiRedactor

        agent = PiiRedactor()
        captured_map: dict = {}

        async def _mock_generate(model_tier, prompt, **kw):
            return "{}", 5, 5

        async def _capture_vault(case_id, encrypted_map):
            fernet = Fernet(_TEST_KEY.encode())
            decrypted = fernet.decrypt(encrypted_map.encode())
            captured_map.update(json.loads(decrypted))

        with (
            patch("agents.pii_redactor.get_client") as mock_get,
            patch("agents.pii_redactor._upsert_pii_vault", side_effect=_capture_vault),
        ):
            mock_client = MagicMock()
            mock_client.generate = _mock_generate
            mock_get.return_value = mock_client

            await agent.run(_PII_CLAIM, _make_ctx())

        # The original PII values should be the keys of the token map
        all_original_values = set(captured_map.keys())
        for pii in ["123-45-6789", "(555) 867-5309", "john.smith@email.com"]:
            assert any(pii in v for v in all_original_values), (
                f"PII '{pii}' not found in token_map keys: {all_original_values}"
            )

    async def test_no_pii_reaches_downstream_prompt(self):
        """The JSON sent to the NER model must not contain raw PII.

        This directly tests SPEC §19: 'No PII from pii_vault appears in any
        logged LLM prompt'.  The regex pass must have already replaced PII
        before the prompt is rendered for the NER call.
        """
        from agents.pii_redactor import PiiRedactor

        agent = PiiRedactor()
        captured_prompts: list[str] = []

        async def _capture_generate(model_tier, prompt, **kw):
            captured_prompts.append(prompt)
            return "{}", 5, 5

        with (
            patch("agents.pii_redactor.get_client") as mock_get,
            patch("agents.pii_redactor._upsert_pii_vault", new_callable=AsyncMock),
        ):
            mock_client = MagicMock()
            mock_client.generate = _capture_generate
            mock_get.return_value = mock_client

            await agent.run(_PII_CLAIM, _make_ctx())

        assert captured_prompts, "No prompt was sent to the model"
        ner_prompt = captured_prompts[0]

        for pii in _PII_PATTERNS_TO_CHECK:
            assert pii not in ner_prompt, (
                f"PII '{pii}' leaked into the NER prompt sent downstream:\n{ner_prompt[:500]}"
            )

    async def test_token_map_encrypted_in_vault(self):
        """The value persisted to PiiVault must be a valid Fernet-encrypted blob."""
        from agents.pii_redactor import PiiRedactor

        agent = PiiRedactor()
        encrypted_blobs: list[str] = []

        async def _mock_generate(model_tier, prompt, **kw):
            return "{}", 5, 5

        async def _capture_vault(case_id, encrypted_map):
            encrypted_blobs.append(encrypted_map)

        with (
            patch("agents.pii_redactor.get_client") as mock_get,
            patch("agents.pii_redactor._upsert_pii_vault", side_effect=_capture_vault),
        ):
            mock_client = MagicMock()
            mock_client.generate = _mock_generate
            mock_get.return_value = mock_client

            await agent.run(_PII_CLAIM, _make_ctx())

        assert encrypted_blobs
        blob = encrypted_blobs[0]
        # Must be decodable with the correct Fernet key
        fernet = Fernet(_TEST_KEY.encode())
        decrypted = fernet.decrypt(blob.encode())
        decoded = json.loads(decrypted)
        assert isinstance(decoded, dict)

    async def test_vault_upsert_called_with_case_id(self):
        """_upsert_pii_vault must be called with the correct case_id."""
        from agents.pii_redactor import PiiRedactor

        agent = PiiRedactor()
        captured_case_ids: list[str] = []

        async def _mock_generate(model_tier, prompt, **kw):
            return "{}", 5, 5

        async def _capture_vault(case_id, encrypted_map):
            captured_case_ids.append(case_id)

        with (
            patch("agents.pii_redactor.get_client") as mock_get,
            patch("agents.pii_redactor._upsert_pii_vault", side_effect=_capture_vault),
        ):
            mock_client = MagicMock()
            mock_client.generate = _mock_generate
            mock_get.return_value = mock_client

            ctx = _make_ctx()
            await agent.run(_PII_CLAIM, ctx)

        assert captured_case_ids == ["case-002"]
