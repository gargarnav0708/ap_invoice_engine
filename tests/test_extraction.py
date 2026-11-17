"""
Unit tests for data extraction, schema validation, and confidence scoring.
"""
import pytest
from types import SimpleNamespace
from pydantic import ValidationError

from app.core.schemas import ExtractedInvoice, ExtractedField, ExtractedLineItem
from app.extraction.confidence import compute_overall_confidence
from app.extraction.llm_provider import MockLLMProvider, OpenAICompatibleProvider
from app.extraction.extraction_prompts import build_strict_extraction_schema


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


def test_live_provider_requests_strict_json_schema(monkeypatch):
    provider = OpenAICompatibleProvider(api_key="test-key", base_url="https://example.invalid/v1", model="test-model")
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
