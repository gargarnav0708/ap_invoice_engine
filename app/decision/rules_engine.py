"""
Deterministic Rules Engine: Aggregates and executes all AP validation rules.
"""
from typing import List, Optional

from app.database.entities import Vendor, PurchaseOrder
from app.core.schemas import (
    NormalizedInvoice, ExtractedInvoice, RuleResult, RuleStatus, RuleSeverity,
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
        line_item_matches: List[LineItemMatch]
    ) -> List[RuleResult]:
        """
        Runs the full suite of AP validation rules.
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
        financial_results = self.financial_validator.validate(normalized, matched_po)
        results.extend(financial_results)

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

        return results
