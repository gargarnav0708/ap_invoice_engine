"""
Confidence scoring calculations for extracted invoice data.
"""
from typing import List
from app.core.schemas import ExtractedInvoice, ExtractedField
from app.config.rules import rules_config


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
