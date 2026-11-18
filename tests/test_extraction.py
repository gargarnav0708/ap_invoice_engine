"""
Unit tests for data extraction, schema validation, and confidence scoring.
"""
import pytest
from types import SimpleNamespace
from pydantic import ValidationError

from app.core.schemas import ExtractedInvoice, ExtractedField, ExtractedLineItem
from app.config.settings import settings
from app.extraction.confidence import compute_overall_confidence
from app.extraction.llm_provider import (
    GroqProvider,
    MockLLMProvider,
    get_llm_provider,
)
from app.extraction.extraction_prompts import build_strict_extraction_schema
from app.extraction.llm_provider import _parse_amount_token
from app.processing.normalizer import normalize_currency, normalize_invoice


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_overall_confidence_must_be_between_zero_and_one(confidence):
    with pytest.raises(ValidationError):
        ExtractedInvoice(overall_confidence=confidence)


def test_extracted_schema_missing_fields_are_null():
    """Missing fields must be None and not fabricated."""
    invoice = ExtractedInvoice(
        vendor_name=ExtractedField(value=None, confidence=0.0),
        invoice_number=ExtractedField(value="INV-123", confidence=0.95),
        total_amount=ExtractedField(value=100.0, confidence=0.95)
    )
    assert invoice.vendor_name.value is None
    assert invoice.vendor_name.confidence == 0.0
    assert invoice.invoice_date.value is None


def test_confidence_calculation():
    """Confidence scoring computes proper weighted average and penalizes missing fields."""
    full_invoice = ExtractedInvoice(
        vendor_name=ExtractedField(value="Acme Corp", confidence=1.0),
        invoice_number=ExtractedField(value="INV-001", confidence=1.0),
        invoice_date=ExtractedField(value="2026-01-01", confidence=1.0),
        currency=ExtractedField(value="USD", confidence=1.0),
        subtotal=ExtractedField(value=100.0, confidence=1.0),
        tax_amount=ExtractedField(value=10.0, confidence=1.0),
        total_amount=ExtractedField(value=110.0, confidence=1.0),
        line_items=[
            ExtractedLineItem(
                description=ExtractedField(value="Widget", confidence=1.0),
                quantity=ExtractedField(value=1.0, confidence=1.0),
                unit_price=ExtractedField(value=100.0, confidence=1.0),
                line_total=ExtractedField(value=100.0, confidence=1.0)
            )
        ]
    )
    full_score = compute_overall_confidence(full_invoice)
    assert full_score >= 0.85

    # Incomplete invoice should have significantly reduced score
    empty_invoice = ExtractedInvoice()
    empty_score = compute_overall_confidence(empty_invoice)
    assert empty_score == 0.0


def test_mock_llm_provider_extraction():
    """Test deterministic mock LLM provider extraction on text."""
    provider = MockLLMProvider()
    sample_text = """
    Acme Industrial Supplies Inc.
    Tax ID: US-TAX-1011
    Invoice Number: INV-2026-999
    Invoice Date: 2026-02-15
    PO Reference: PO-2026-001
    Subtotal: $1,250.00
    Tax: $100.00
    Total Amount: $1,350.00
    ITEM: Industrial Safety Helmets | Qty: 20 | Price: $50.00 | Total: $1000.00
    """
    extracted = provider.extract_invoice(sample_text)
    assert extracted.is_mock is True
    assert extracted.invoice_number.value == "INV-2026-999"
    assert extracted.po_reference.value == "PO-2026-001"
    assert extracted.vendor_tax_id.value == "US-TAX-1011"
    assert extracted.total_amount.value == 1350.00
    assert len(extracted.line_items) == 1
    assert extracted.line_items[0].description.value == "Industrial Safety Helmets"


def test_mock_extractor_retains_page_evidence_and_missing_values():
    provider = MockLLMProvider()
    extracted = provider.extract_invoice(
        "--- Page 1 ---\nVendor: Acme Inc.\nInvoice Number: INV-PAGED\n"
        "--- Page 2 ---\nTotal Amount: $25.00"
    )

    assert extracted.invoice_number.page == 1
    assert extracted.total_amount.page == 2
    assert extracted.po_reference.value is None
    assert extracted.po_reference.page is None


def test_strict_extraction_schema_marks_every_object_property_required():
    schema = build_strict_extraction_schema()

    def check(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])
            for child in node["properties"].values():
                check(child)
        elif node.get("type") == "array":
            check(node["items"])

    check(schema)
    assert schema["properties"]["vendor_name"]["properties"]["page"]["type"] == ["integer", "null"]
    assert schema["properties"]["line_items"]["items"]["properties"]["description"]["properties"]["page"]["type"] == ["integer", "null"]
    assert "paid_date" in schema["properties"]


def test_live_provider_requests_strict_json_schema(monkeypatch):
    provider = GroqProvider(api_key="test-key", base_url="https://example.invalid/v1", model="openai/gpt-oss-20b")
    captured = {}
    payload = {
        "vendor_name": {"value": "Acme Inc", "confidence": 0.9, "evidence": "Acme Inc", "page": 1},
        "invoice_number": {"value": "INV-1", "confidence": 0.9, "evidence": "INV-1", "page": 1},
        "total_amount": {"value": 100.0, "confidence": 0.9, "evidence": "Total 100.00", "page": 1},
        "currency": {"value": "USD", "confidence": 0.9, "evidence": "USD", "page": 1},
        "line_items": [],
    }

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=__import__("json").dumps(payload)))])

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = provider.extract_invoice("Acme Inc invoice INV-1 total 100 USD")

    assert result.invoice_number.value == "INV-1"
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert captured["max_completion_tokens"] == settings.LLM_MAX_COMPLETION_TOKENS
    assert captured["reasoning_effort"] == "low"
    assert captured["extra_body"] == {"include_reasoning": False}
    assert "include_reasoning" not in captured

    # Verify all passed kwargs are accepted by the real OpenAI SDK Completions.create signature
    import inspect
    from openai.resources.chat.completions import Completions

    sig = inspect.signature(Completions.create)
    for kw in captured:
        assert kw in sig.parameters, f"Unsupported keyword argument for Completions.create: {kw}"


def test_live_provider_retries_completion_token_truncation_with_larger_budget(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_COMPLETION_TOKENS", 1024)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_COMPLETION_TOKENS", 2048)
    provider = GroqProvider(api_key="test-key", base_url="https://example.invalid/v1", model="openai/gpt-oss-20b")
    requests = []
    payload = {"invoice_number": {"value": "INV-1", "confidence": 0.9}}

    class TruncatedCompletionError(Exception):
        status_code = 400

    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise TruncatedCompletionError(
                "max completion tokens reached before generating a valid document; output was truncated"
            )
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content=__import__("json").dumps(payload)),
        )])

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = provider.extract_invoice("Invoice INV-1")

    assert result.invoice_number.value == "INV-1"
    assert [request["max_completion_tokens"] for request in requests] == [1024, 2048]


def test_live_provider_retries_response_with_length_finish_reason(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_COMPLETION_TOKENS", 1024)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_COMPLETION_TOKENS", 2048)
    provider = GroqProvider(api_key="test-key", base_url="https://example.invalid/v1", model="test-model")
    requests = []
    payload = {"invoice_number": {"value": "INV-2", "confidence": 0.9}}

    def create(**kwargs):
        requests.append(kwargs)
        content = "{\"invoice_number\":" if len(requests) == 1 else __import__("json").dumps(payload)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="length" if len(requests) == 1 else "stop",
            message=SimpleNamespace(content=content),
        )])

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = provider.extract_invoice("Invoice INV-2")

    assert result.invoice_number.value == "INV-2"
    assert [request["max_completion_tokens"] for request in requests] == [1024, 2048]


def test_mock_extractor_keeps_paid_date_distinct_and_does_not_guess_bare_dollar_currency():
    extracted = MockLLMProvider().extract_invoice(
        "Invoice Date Jun 22, 2021\nDue Date Jun 27,2021\nPaid (Jun 22, 2021)\nTotal $1,564.00"
    )
    normalized = normalize_invoice(extracted)
    assert normalized.invoice_date == "2021-06-22"
    assert normalized.due_date == "2021-06-27"
    assert normalized.paid_date == "2021-06-22"
    assert extracted.currency.value is None
    assert extracted.currency.evidence == "$"
    assert normalize_currency("$") is None
    assert normalize_currency("HK$") == "HKD"
    assert normalize_currency("USD") == "USD"


def test_amount_parser_repairs_common_ocr_decimal_separator_error():
    assert _parse_amount_token("1,564,00") == 1564.0
    assert _parse_amount_token("1.564,00") == 1564.0
    assert _parse_amount_token("1,564.00") == 1564.0


def test_live_provider_retries_groq_json_validation_error():
    provider = GroqProvider(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    request_formats = []
    payload = {
        "invoice_number": {"value": "INV-1", "confidence": 0.9},
        "total_amount": {"value": 100.0, "confidence": 0.9},
    }

    class JSONValidationError(Exception):
        status_code = 400

    def create(**kwargs):
        request_formats.append(kwargs["response_format"]["type"])
        if len(request_formats) == 1:
            raise JSONValidationError("Failed to validate JSON")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=__import__("json").dumps(payload)
                    )
                )
            ]
        )

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = provider.extract_invoice("Invoice INV-1, total $100.00")

    assert request_formats == ["json_schema", "json_object"]
    assert result.invoice_number.value == "INV-1"
    assert result.total_amount.value == 100.0


def test_live_provider_uses_configured_groq_endpoint(monkeypatch):
    endpoint = "https://api.groq.com/openai/v1"
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-groq-key")
    monkeypatch.setattr(settings, "LLM_BASE_URL", endpoint)
    monkeypatch.setattr(settings, "LLM_MODEL", "openai/gpt-oss-20b")

    provider = get_llm_provider(mode="live")

    assert isinstance(provider, GroqProvider)
    assert str(provider.client.base_url).startswith(endpoint)
    assert provider.model == "openai/gpt-oss-20b"
