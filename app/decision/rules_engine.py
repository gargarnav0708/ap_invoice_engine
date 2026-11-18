"""
Deterministic Rules Engine: Aggregates and executes all AP validation rules.
"""
from typing import List, Optional

from app.database.entities import Vendor, PurchaseOrder
from app.core.schemas import (
    NormalizedInvoice, ExtractedInvoice, DocumentType, RuleResult, RuleStatus, RuleSeverity,
    LineItemMatch
)
from app.config.rules import rules_config
from app.processing.duplicate_detector import DuplicateCheckResult
from app.processing.financial_validator import FinancialValidator
from app.utils.logging import get_logger

logger = get_logger("rules_engine")


class RulesEngine:
    def __init__(self):
        self.financial_validator = FinancialValidator()

    def evaluate_all_rules(
        self,
        extracted: ExtractedInvoice,
        normalized: NormalizedInvoice,
        matched_vendor: Optional[Vendor],
        vendor_status: str,
        matched_po: Optional[PurchaseOrder],
        po_status: str,
        duplicate_check: DuplicateCheckResult,
        line_item_matches: List[LineItemMatch],
        financial_results: Optional[List[RuleResult]] = None
    ) -> List[RuleResult]:
        """
        Runs the full suite of AP validation rules.

        ``financial_results`` may be supplied by the orchestrator when the
        financial / PO-balance checks have already been executed (so the
        pipeline's financial-validation stages report real outcomes without
        running the validator twice). If omitted, they are computed here.
        """
        results: List[RuleResult] = []

        # ======================================================================
        # 1. Extraction Rules
        # ======================================================================
        # Check critical fields
        missing_critical = []
        for field_name in rules_config.extraction.CRITICAL_FIELDS:
            field_obj = getattr(extracted, field_name, None)
            if not field_obj or field_obj.value is None:
                missing_critical.append(field_name)

        if missing_critical:
            results.append(
                RuleResult(
                    rule_id="RULE_MISSING_CRITICAL_FIELD",
                    rule_name="Critical Invoice Fields Present",
                    category="EXTRACTION",
                    result=RuleStatus.FAIL,
                    severity=RuleSeverity.SOFT,
                    expected_value="All critical fields present",
                    actual_value=f"Missing: {', '.join(missing_critical)}",
                    explanation=f"Critical fields missing from invoice: {', '.join(missing_critical)}. Requires manual review."
                )
            )
        else:
            results.append(
                RuleResult(
                    rule_id="RULE_MISSING_CRITICAL_FIELD",
                    rule_name="Critical Invoice Fields Present",
                    category="EXTRACTION",
                    result=RuleStatus.PASS,
                    severity=RuleSeverity.INFO,
                    explanation="All critical fields successfully extracted."
                )
            )

        # Extraction confidence check
        if extracted.overall_confidence < rules_config.extraction.MIN_OVERALL_CONFIDENCE_THRESHOLD:
            results.append(
                RuleResult(
                    rule_id="RULE_LOW_EXTRACTION_CONFIDENCE",
                    rule_name="Extraction Confidence Threshold",
                    category="EXTRACTION",
                    result=RuleStatus.WARN,
                    severity=RuleSeverity.SOFT,
                    expected_value=f">= {rules_config.extraction.MIN_OVERALL_CONFIDENCE_THRESHOLD:.2f}",
                    actual_value=f"{extracted.overall_confidence:.2f}",
                    explanation=(
                        f"Overall extraction confidence ({extracted.overall_confidence:.2f}) "
                        f"is below threshold ({rules_config.extraction.MIN_OVERALL_CONFIDENCE_THRESHOLD:.2f})."
                    )
                )
            )

        low_ocr_fields = {
            field_name: confidence
            for field_name, confidence in extracted.ocr_field_confidences.items()
            if confidence < rules_config.extraction.MIN_OCR_CONFIDENCE_THRESHOLD
            and getattr(extracted, field_name, None) is not None
            and getattr(extracted, field_name).value is not None
        }
        low_document_ocr = (
            extracted.ocr_confidence is not None
            and extracted.ocr_confidence < rules_config.extraction.MIN_OCR_CONFIDENCE_THRESHOLD
        )
        if low_document_ocr or low_ocr_fields:
            field_summary = ", ".join(
                f"{name} {confidence:.1f}%" for name, confidence in low_ocr_fields.items()
            )
            actual_confidence = (
                f"{extracted.ocr_confidence:.1f}% document mean" if extracted.ocr_confidence is not None else "not available"
            )
            if field_summary:
                actual_confidence += f"; low-evidence fields: {field_summary}"
            results.append(
                RuleResult(
                    rule_id="RULE_LOW_OCR_CONFIDENCE",
                    rule_name="OCR Word Confidence Threshold",
                    category="EXTRACTION",
                    result=RuleStatus.WARN,
                    severity=RuleSeverity.SOFT,
                    expected_value=f">= {rules_config.extraction.MIN_OCR_CONFIDENCE_THRESHOLD:.0f}%",
                    actual_value=actual_confidence,
                    explanation=(
                        "OCR confidence for the document or one or more critical evidence snippets "
                        f"is below the review threshold ({rules_config.extraction.MIN_OCR_CONFIDENCE_THRESHOLD:.0f}%). "
                        "Verify dates, currency, and amounts against the source image."
                    ),
                )
            )

        if extracted.document_type in {DocumentType.SCANNED_IMAGE, DocumentType.HYBRID} and not extracted.line_items:
            results.append(
                RuleResult(
                    rule_id="RULE_SCANNED_INVOICE_NO_LINE_ITEMS",
                    rule_name="Scanned Invoice Line Item Coverage",
                    category="EXTRACTION",
                    result=RuleStatus.WARN,
                    severity=RuleSeverity.SOFT,
                    expected_value="Line items extracted or verified by a reviewer",
                    actual_value="No line items extracted from scanned content",
                    explanation=(
                        "No line items were extracted from this scanned or hybrid invoice. "
                        "Check the source image and totals before approval."
                    ),
                )
            )

        # ======================================================================
        # 2. Duplicate Detection Rules
        # ======================================================================
        if duplicate_check.is_duplicate:
            if duplicate_check.severity == "CONFIRMED":
                results.append(
                    RuleResult(
                        rule_id="RULE_CONFIRMED_DUPLICATE",
                        rule_name="Confirmed Duplicate Invoice Check",
                        category="DUPLICATE",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value="Unique invoice document",
                        actual_value=f"Duplicate of Invoice ID {duplicate_check.matched_invoice_id}",
                        explanation=duplicate_check.explanation
                    )
                )
            elif duplicate_check.severity == "POTENTIAL":
                results.append(
                    RuleResult(
                        rule_id="RULE_POTENTIAL_DUPLICATE",
                        rule_name="Potential Near-Duplicate Check",
                        category="DUPLICATE",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value="No similar prior invoices",
                        actual_value=f"Similar to Invoice ID {duplicate_check.matched_invoice_id}",
                        explanation=duplicate_check.explanation
                    )
                )
        else:
            results.append(
                RuleResult(
                    rule_id="RULE_DUPLICATE_CHECK",
                    rule_name="Duplicate Invoice Check",
                    category="DUPLICATE",
                    result=RuleStatus.PASS,
                    severity=RuleSeverity.INFO,
                    explanation="No duplicate invoice detected."
                )
            )

        # ======================================================================
        # 3. Vendor Validation Rules
        # ======================================================================
        if not matched_vendor:
            results.append(
                RuleResult(
                    rule_id="RULE_VENDOR_EXISTS",
                    rule_name="Vendor Registered Check",
                    category="VENDOR",
                    result=RuleStatus.FAIL,
                    severity=RuleSeverity.SOFT,
                    expected_value="Registered vendor in system",
                    actual_value=f"'{normalized.vendor_name}' not found",
                    explanation="Vendor is not registered in the system or could not be identified."
                )
            )
        else:
            # Check if vendor is approved
            if not matched_vendor.is_approved:
                results.append(
                    RuleResult(
                        rule_id="RULE_VENDOR_UNAPPROVED",
                        rule_name="Vendor Approval Status Check",
                        category="VENDOR",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value="Approved / Active Vendor",
                        actual_value="Unapproved / Suspended Vendor",
                        explanation=f"Vendor '{matched_vendor.legal_name}' is explicitly marked as unapproved/inactive."
                    )
                )
            else:
                results.append(
                    RuleResult(
                        rule_id="RULE_VENDOR_APPROVED",
                        rule_name="Vendor Approval Status Check",
                        category="VENDOR",
                        result=RuleStatus.PASS,
                        severity=RuleSeverity.INFO,
                        explanation=f"Vendor '{matched_vendor.legal_name}' is verified and approved."
                    )
                )

            # Vendor ambiguity check
            if vendor_status == "AMBIGUOUS_MATCH":
                results.append(
                    RuleResult(
                        rule_id="RULE_VENDOR_AMBIGUOUS",
                        rule_name="Vendor Match Confidence",
                        category="VENDOR",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value="Unambiguous vendor match",
                        actual_value=f"Fuzzy candidate '{matched_vendor.legal_name}'",
                        explanation="Vendor matching is ambiguous or score is moderate; requires confirmation."
                    )
                )

        # ======================================================================
        # 4. Purchase Order Validation Rules
        # ======================================================================
        if not matched_po:
            results.append(
                RuleResult(
                    rule_id="RULE_PO_EXISTS",
                    rule_name="Purchase Order Existence Check",
                    category="PO",
                    result=RuleStatus.FAIL,
                    severity=RuleSeverity.SOFT,
                    expected_value="Valid PO in procurement system",
                    actual_value=f"PO '{normalized.po_reference}' not found",
                    explanation="No corresponding Purchase Order could be identified."
                )
            )
        else:
            # Check PO status
            if matched_po.status != "OPEN":
                results.append(
                    RuleResult(
                        rule_id="RULE_PO_CLOSED_OR_CANCELLED",
                        rule_name="PO Active Status Check",
                        category="PO",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value="OPEN",
                        actual_value=matched_po.status,
                        explanation=f"Purchase Order '{matched_po.po_number}' has status '{matched_po.status}'."
                    )
                )
            else:
                results.append(
                    RuleResult(
                        rule_id="RULE_PO_ACTIVE",
                        rule_name="PO Active Status Check",
                        category="PO",
                        result=RuleStatus.PASS,
                        severity=RuleSeverity.INFO,
                        explanation=f"Purchase Order '{matched_po.po_number}' is active (OPEN)."
                    )
                )

            # Check PO vendor match
            if matched_vendor and matched_po.vendor_id != matched_vendor.id:
                results.append(
                    RuleResult(
                        rule_id="RULE_PO_VENDOR_MISMATCH",
                        rule_name="PO Belongs to Matched Vendor",
                        category="PO",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value=f"PO vendor ID {matched_po.vendor_id}",
                        actual_value=f"Invoice vendor ID {matched_vendor.id}",
                        explanation=f"Purchase Order '{matched_po.po_number}' belongs to a different vendor."
                    )
                )

            # PO ambiguity
            if po_status == "AMBIGUOUS_MATCH":
                results.append(
                    RuleResult(
                        rule_id="RULE_PO_AMBIGUOUS",
                        rule_name="PO Match Confidence",
                        category="PO",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value="Unambiguous PO link",
                        actual_value=f"Inferred PO '{matched_po.po_number}'",
                        explanation="Purchase order was inferred or multiple viable POs exist. Human review required."
                    )
                )

        # ======================================================================
        # 5. Financial Validation & PO Balance Rules
        # ======================================================================
        if financial_results is None:
            financial_results = self.financial_validator.validate(normalized, matched_po)
        results.extend(financial_results)

        # Tax-rate reasonableness: tax should be a plausible fraction of subtotal.
        tax_amt = normalized.tax_amount or 0.0
        sub_amt = normalized.subtotal
        if sub_amt and sub_amt > 0 and tax_amt > 0:
            tax_rate = tax_amt / sub_amt
            max_rate = rules_config.financial.MAX_REASONABLE_TAX_RATE_PCT
            if tax_rate > max_rate:
                results.append(
                    RuleResult(
                        rule_id="RULE_TAX_CALCULATION_DISCREPANCY",
                        rule_name="Tax Rate Reasonableness",
                        category="FINANCIAL",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value=f"<= {max_rate*100:.0f}% of subtotal",
                        actual_value=f"{tax_rate*100:.1f}%",
                        explanation=(
                            f"Declared tax (${tax_amt:.2f}) is {tax_rate*100:.1f}% of subtotal "
                            f"(${sub_amt:.2f}), above the {max_rate*100:.0f}% reasonableness bound. Verify tax."
                        )
                    )
                )

        # ======================================================================
        # 6. Line Item Validation Rules
        # ======================================================================
        if line_item_matches:
            unmatched_items = [m for m in line_item_matches if m.po_line_id is None]
            if unmatched_items and matched_po:
                results.append(
                    RuleResult(
                        rule_id="RULE_LINE_ITEM_MISMATCH",
                        rule_name="Invoice Line Items Matched to PO",
                        category="LINE_ITEM",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value="All line items matched to PO",
                        actual_value=f"{len(unmatched_items)} items unmatched",
                        explanation=f"{len(unmatched_items)} invoice line items could not be matched to PO lines."
                    )
                )

            # Price & quantity tolerance checks on matched lines.
            price_review_pct = rules_config.matching.LINE_ITEM_PRICE_REVIEW_PCT
            price_note_pct = rules_config.matching.LINE_ITEM_PRICE_TOLERANCE_PCT
            qty_tol = rules_config.matching.LINE_ITEM_QTY_OVERAGE_TOLERANCE_ABS
            price_review_lines = []
            qty_over_lines = []

            for m in line_item_matches:
                if m.po_line_id is None:
                    continue
                inv_item = (
                    normalized.line_items[m.invoice_line_idx]
                    if 0 <= m.invoice_line_idx < len(normalized.line_items) else None
                )

                # Unit-price variance (invoice line vs PO line).
                if m.price_diff is not None and inv_item is not None:
                    po_unit_price = round(inv_item.unit_price - m.price_diff, 2)
                    if po_unit_price > 0:
                        var_pct = abs(m.price_diff) / po_unit_price
                        line_ref = m.po_line_number or (m.invoice_line_idx + 1)
                        if var_pct > price_review_pct:
                            price_review_lines.append(
                                f"line {line_ref}: ${po_unit_price:.2f} PO vs "
                                f"${inv_item.unit_price:.2f} invoiced ({var_pct*100:.1f}%)"
                            )
                        elif var_pct > price_note_pct:
                            results.append(
                                RuleResult(
                                    rule_id="RULE_LINE_ITEM_PRICE_VARIANCE",
                                    rule_name="Line Item Price Within Tolerance",
                                    category="LINE_ITEM",
                                    result=RuleStatus.PASS,
                                    severity=RuleSeverity.INFO,
                                    expected_value=f"${po_unit_price:.2f} (PO)",
                                    actual_value=f"${inv_item.unit_price:.2f}",
                                    difference=f"{var_pct*100:.1f}%",
                                    explanation=(
                                        f"Line {line_ref} unit price varies {var_pct*100:.1f}% from PO, "
                                        f"within the {price_review_pct*100:.0f}% review tolerance. Approved with note."
                                    )
                                )
                            )

                # Quantity overage (invoice billed more units than the PO line).
                if m.qty_diff is not None and m.qty_diff > qty_tol:
                    line_ref = m.po_line_number or (m.invoice_line_idx + 1)
                    qty_over_lines.append(f"line {line_ref}: +{m.qty_diff:g} over PO")

            if price_review_lines:
                results.append(
                    RuleResult(
                        rule_id="RULE_LINE_ITEM_PRICE_VARIANCE",
                        rule_name="Line Item Price Variance Exceeds Tolerance",
                        category="LINE_ITEM",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value=f"<= {price_review_pct*100:.0f}% vs PO unit price",
                        actual_value=f"{len(price_review_lines)} line(s) over tolerance",
                        explanation=(
                            "Unit price exceeds PO beyond tolerance on "
                            + "; ".join(price_review_lines)
                            + ". Human review required."
                        )
                    )
                )

            if qty_over_lines:
                results.append(
                    RuleResult(
                        rule_id="RULE_LINE_ITEM_QTY_OVERAGE",
                        rule_name="Line Item Quantity Overage",
                        category="LINE_ITEM",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value="Invoiced qty <= PO qty",
                        actual_value=f"{len(qty_over_lines)} line(s) over PO",
                        explanation=(
                            "Invoiced quantity exceeds the PO on "
                            + "; ".join(qty_over_lines)
                            + ". Human review required."
                        )
                    )
                )

        return results
