"""
Confidence scoring calculations for extracted invoice data.
"""
from typing import List, Optional, Dict, Any
import re
from app.core.schemas import ExtractedInvoice, ExtractedField
from app.config.rules import rules_config


def compute_evidence_ocr_confidence(evidence: Optional[str], words: List[Dict[str, Any]]) -> Optional[float]:
    """Score the Tesseract word confidences supporting one extracted evidence snippet."""
    if not evidence or not words:
        return None

    def tokens(value: str) -> List[str]:
        return re.findall(r"[A-Za-z]+|\d[\d,./-]*|[$€£₹]", value)

    def normalize(token: str) -> str:
        if token in {"$", "€", "£", "₹"}:
            return token
        return re.sub(r"[^a-z0-9]", "", token.lower())

    evidence_tokens = [normalize(token) for token in tokens(evidence)]
    ocr_tokens = []
    for word in words:
        raw_text = str(word.get("text", ""))
        try:
            confidence = float(word.get("confidence"))
        except (TypeError, ValueError):
            continue
        for token in tokens(raw_text):
            ocr_tokens.append((normalize(token), raw_text, confidence))
    if not evidence_tokens or not ocr_tokens:
        return None

    matched_confidences = []
    for token in evidence_tokens:
        match = next(
            (confidence for candidate, raw, confidence in ocr_tokens
             if token == candidate or (token in {"$", "€", "£", "₹"} and token in raw)),
            None,
        )
        if match is not None:
            matched_confidences.append(match)
    coverage = len(matched_confidences) / len(evidence_tokens)
    mean_confidence = sum(matched_confidences) / len(matched_confidences) if matched_confidences else 0.0
    return round(mean_confidence * coverage, 1)


def compute_overall_confidence(invoice: ExtractedInvoice) -> float:
    """
    Computes weighted overall extraction confidence across critical and non-critical fields.
    """
    weights = {
        "vendor_name": 0.20,
        "invoice_number": 0.20,
        "total_amount": 0.20,
        "invoice_date": 0.10,
        "currency": 0.10,
        "subtotal": 0.05,
        "tax_amount": 0.05,
        "line_items": 0.10,
    }

    score = 0.0

    # Critical single fields
    for field_name in ["vendor_name", "invoice_number", "total_amount", "invoice_date", "currency", "subtotal", "tax_amount"]:
        field_obj: ExtractedField = getattr(invoice, field_name)
        weight = weights.get(field_name, 0.05)
        if field_obj.value is not None:
            score += weight * field_obj.confidence
        else:
            # Missing critical field gets zero score for that portion
            score += 0.0

    # Line items confidence
    if invoice.line_items:
        item_confidences = []
        for item in invoice.line_items:
            item_score = (
                item.description.confidence * 0.4 +
                item.quantity.confidence * 0.2 +
                item.unit_price.confidence * 0.2 +
                item.line_total.confidence * 0.2
            )
            item_confidences.append(item_score)
        avg_line_conf = sum(item_confidences) / len(item_confidences)
        score += weights["line_items"] * avg_line_conf
    else:
        # No line items extracted
        score += 0.0

    return round(min(1.0, max(0.0, score)), 3)
