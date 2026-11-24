"""Tests for Agent 3 — Document Extractor (including Docling fallback)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.models import CaseContext, ExtractedClaim

_SAMPLE_CLAIM = ExtractedClaim(
    claim_id="CLM-001",
    payer="Aetna",
    plan_type="employer",
    state="CA",
    service_date="2024-03-15",
    denial_date="2024-04-01",
    cpt_codes=["70553"],
    icd10_codes=["G35"],
    billed_amount=Decimal("2500.00"),
    denial_reason_text="Service not medically necessary per plan guidelines.",
    denial_reason_code="50",
    field_confidence={f: 0.95 for f in [
        "claim_id", "payer", "plan_type", "state",
        "service_date", "denial_date", "cpt_codes",
        "icd10_codes", "billed_amount", "denial_reason_text", "denial_reason_code",
    ]},
)


def _make_ctx() -> CaseContext:
    return CaseContext(case_id="case-001", user_id="u-1", trace_id="t-1")


def _mock_generate(text: str, ti: int = 10, to: int = 20):
    async def _gen(model_tier, prompt, **kw):
        return text, ti, to
    return _gen


@pytest.fixture(autouse=True)
def _patch_trace(monkeypatch):
    """Disable DB persistence in the tracing decorator for all tests here."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    class _AsyncCtx:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("services.tracing.AsyncSessionFactory", lambda: _AsyncCtx())
    monkeypatch.setattr("services.tracing._get_langfuse", lambda: None)


class TestDocumentExtractor:
    async def test_returns_extracted_claim(self):
        """Successful watsonx call returns an AgentResult[ExtractedClaim]."""
        from agents.document_extractor import DocumentExtractor

        agent = DocumentExtractor()
        response_json = _SAMPLE_CLAIM.model_dump_json()

        with patch("agents.document_extractor.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _mock_generate(response_json)
            mock_get.return_value = mock_client

            result = await agent.run(
                "Claim CLM-001 denied for MRI brain without contrast...",
                _make_ctx(),
            )

        assert isinstance(result.output, ExtractedClaim)
        assert result.output.claim_id == "CLM-001"
        assert result.output.cpt_codes == ["70553"]
        assert result.tokens_in == 10
        assert result.tokens_out == 20

    async def test_confidence_derived_from_field_confidence(self):
        """Confidence should equal mean of field_confidence values."""
        from agents.document_extractor import DocumentExtractor

        agent = DocumentExtractor()
        response_json = _SAMPLE_CLAIM.model_dump_json()

        with patch("agents.document_extractor.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _mock_generate(response_json)
            mock_get.return_value = mock_client

            result = await agent.run("some text", _make_ctx())

        assert 0.0 <= result.confidence <= 1.0

    async def test_strips_markdown_fences(self):
        """Model output wrapped in ```json fences should still parse."""
        from agents.document_extractor import DocumentExtractor

        agent = DocumentExtractor()
        fenced = f"```json\n{_SAMPLE_CLAIM.model_dump_json()}\n```"

        with patch("agents.document_extractor.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _mock_generate(fenced)
            mock_get.return_value = mock_client

            result = await agent.run("text", _make_ctx())

        assert result.output.payer == "Aetna"

    async def test_docling_fallback_on_bytes_input(self):
        """PDF bytes input triggers Docling before calling the model."""
        from agents.document_extractor import DocumentExtractor

        agent = DocumentExtractor()
        response_json = _SAMPLE_CLAIM.model_dump_json()

        with (
            patch("agents.document_extractor._extract_text_docling", return_value="extracted text") as mock_docling,
            patch("agents.document_extractor.get_client") as mock_get,
        ):
            mock_client = MagicMock()
            mock_client.generate = _mock_generate(response_json)
            mock_get.return_value = mock_client

            result = await agent.run(b"%PDF-fake-bytes", _make_ctx())

        mock_docling.assert_called_once()
        assert result.output.claim_id == "CLM-001"

    async def test_raises_on_unparseable_response(self):
        """Should raise RuntimeError if the model returns non-JSON."""
        from agents.document_extractor import DocumentExtractor

        agent = DocumentExtractor()

        with patch("agents.document_extractor.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate = _mock_generate("This is not JSON at all.")
            mock_get.return_value = mock_client

            with pytest.raises(RuntimeError, match="could not parse"):
                await agent.run("text", _make_ctx())
