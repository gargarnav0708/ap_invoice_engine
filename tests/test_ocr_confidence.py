from app.core.schemas import ExtractedField, ExtractedInvoice, NormalizedInvoice, DecisionStatus
from app.decision.decision_service import DecisionService
from app.decision.rules_engine import RulesEngine
from app.processing.duplicate_detector import DuplicateCheckResult
from app.extraction.confidence import compute_evidence_ocr_confidence


def test_low_ocr_confidence_creates_review_warning():
    extracted = ExtractedInvoice(ocr_confidence=43.0)
    results = RulesEngine().evaluate_all_rules(
        extracted=extracted,
        normalized=NormalizedInvoice(),
        matched_vendor=None,
        vendor_status="NO_MATCH",
        matched_po=None,
        po_status="NO_MATCH",
        duplicate_check=DuplicateCheckResult(False, "NONE", "NONE"),
        line_item_matches=[],
        financial_results=[],
    )

    warning = next(result for result in results if result.rule_id == "RULE_LOW_OCR_CONFIDENCE")
    decision = DecisionService().determine_decision(results)
    assert warning.actual_value.startswith("43.0%")
    assert decision.status == DecisionStatus.NEEDS_REVIEW


def test_low_confidence_for_amount_evidence_routes_review_even_when_page_average_is_high():
    extracted = ExtractedInvoice(
        total_amount=ExtractedField(value=8156400.0, confidence=0.99, evidence="Total 8156400"),
        ocr_confidence=90.0,
        ocr_field_confidences={"total_amount": 45.0},
    )
    results = RulesEngine().evaluate_all_rules(
        extracted=extracted,
        normalized=NormalizedInvoice(),
        matched_vendor=None,
        vendor_status="NO_MATCH",
        matched_po=None,
        po_status="NO_MATCH",
        duplicate_check=DuplicateCheckResult(False, "NONE", "NONE"),
        line_item_matches=[],
        financial_results=[],
    )
    warning = next(result for result in results if result.rule_id == "RULE_LOW_OCR_CONFIDENCE")
    assert "total_amount 45.0%" in warning.actual_value
    assert DecisionService().determine_decision(results).status == DecisionStatus.NEEDS_REVIEW


def test_evidence_ocr_confidence_penalizes_words_not_found_in_ocr():
    words = [
        {"text": "Total", "confidence": 90.0},
        {"text": "$1,564.00", "confidence": 95.0},
    ]
    assert compute_evidence_ocr_confidence("Total 8156400", words) == 45.0
