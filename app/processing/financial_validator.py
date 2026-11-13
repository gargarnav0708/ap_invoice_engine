"""
Deterministic Financial Validator enforcing AP business rules, calculations, and PO balances.
"""
from typing import List, Optional
from app.database.entities import PurchaseOrder
from app.core.schemas import NormalizedInvoice, RuleResult, RuleStatus, RuleSeverity
from app.config.rules import rules_config
from app.utils.logging import get_logger

logger = get_logger("financial_validator")


class FinancialValidator:
    def validate(
        self,
        invoice: NormalizedInvoice,
        po: Optional[PurchaseOrder] = None
    ) -> List[RuleResult]:
        """
        Runs comprehensive financial validation checks against an invoice and matched PO.
        """
        results: List[RuleResult] = []
        tol_abs = rules_config.financial.ARITHMETIC_TOLERANCE_ABS

        # 1. Line items arithmetic: quantity * unit_price == line_total
        if invoice.line_items:
            for idx, item in enumerate(invoice.line_items):
                expected_total = round(item.quantity * item.unit_price, 2)
                diff = abs(item.line_total - expected_total)
                if diff > tol_abs:
                    results.append(
                        RuleResult(
                            rule_id=f"RULE_LINE_MATH_LINE_{idx+1}",
                            rule_name=f"Line Item {idx+1} Math Check",
                            category="FINANCIAL",
                            result=RuleStatus.FAIL,
                            severity=RuleSeverity.HARD,
                            expected_value=f"{expected_total:.2f}",
                            actual_value=f"{item.line_total:.2f}",
                            difference=f"{diff:.2f}",
                            explanation=f"Line {idx+1} calculation mismatch: {item.quantity} * ${item.unit_price:.2f} = ${expected_total:.2f}, but line total is ${item.line_total:.2f}."
                        )
                    )

            # 2. Subtotal arithmetic: sum of line items == invoice.subtotal
            computed_subtotal = round(sum(item.line_total for item in invoice.line_items), 2)
            if invoice.subtotal is not None:
                sub_diff = abs(invoice.subtotal - computed_subtotal)
                if sub_diff <= tol_abs:
                    results.append(
                        RuleResult(
                            rule_id="RULE_SUBTOTAL_LINE_SUM",
                            rule_name="Subtotal Matches Line Items Sum",
                            category="FINANCIAL",
                            result=RuleStatus.PASS,
                            severity=RuleSeverity.INFO,
                            expected_value=f"{computed_subtotal:.2f}",
                            actual_value=f"{invoice.subtotal:.2f}",
                            difference=f"{sub_diff:.2f}",
                            explanation="Sum of individual line totals matches declared invoice subtotal."
                        )
                    )
                else:
                    results.append(
                        RuleResult(
                            rule_id="RULE_SUBTOTAL_LINE_SUM",
                            rule_name="Subtotal Matches Line Items Sum",
                            category="FINANCIAL",
                            result=RuleStatus.FAIL,
                            severity=RuleSeverity.SOFT,
                            expected_value=f"{computed_subtotal:.2f}",
                            actual_value=f"{invoice.subtotal:.2f}",
                            difference=f"{sub_diff:.2f}",
                            explanation=f"Sum of line items (${computed_subtotal:.2f}) does not match declared subtotal (${invoice.subtotal:.2f})."
                        )
                    )

        # 3. Total amount math: subtotal + tax == total_amount
        if invoice.total_amount is not None:
            effective_sub = invoice.subtotal if invoice.subtotal is not None else (
                round(sum(item.line_total for item in invoice.line_items), 2) if invoice.line_items else None
            )

            if effective_sub is not None:
                expected_total = round(effective_sub + (invoice.tax_amount or 0.0), 2)
                total_diff = abs(invoice.total_amount - expected_total)
                if total_diff <= tol_abs:
                    results.append(
                        RuleResult(
                            rule_id="RULE_TOTAL_SUM_CHECK",
                            rule_name="Total Amount Math Check",
                            category="FINANCIAL",
                            result=RuleStatus.PASS,
                            severity=RuleSeverity.INFO,
                            expected_value=f"{expected_total:.2f}",
                            actual_value=f"{invoice.total_amount:.2f}",
                            difference=f"{total_diff:.2f}",
                            explanation="Invoice total correctly equals subtotal plus tax."
                        )
                    )
                else:
                    results.append(
                        RuleResult(
                            rule_id="RULE_TOTAL_SUM_CHECK",
                            rule_name="Total Amount Math Check",
                            category="FINANCIAL",
                            result=RuleStatus.FAIL,
                            severity=RuleSeverity.HARD,
                            expected_value=f"{expected_total:.2f}",
                            actual_value=f"{invoice.total_amount:.2f}",
                            difference=f"{total_diff:.2f}",
                            explanation=f"Declared total (${invoice.total_amount:.2f}) does not equal subtotal + tax (${expected_total:.2f})."
                        )
                    )

        # 4. PO Balance & Split Invoice Validation
        if po:
            # Currency check
            if invoice.currency and po.currency and invoice.currency != po.currency:
                results.append(
                    RuleResult(
                        rule_id="RULE_CURRENCY_MISMATCH",
                        rule_name="Currency Consistency Check",
                        category="FINANCIAL",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value=po.currency,
                        actual_value=invoice.currency,
                        explanation=f"Invoice currency ({invoice.currency}) does not match Purchase Order currency ({po.currency})."
                    )
                )
            else:
                results.append(
                    RuleResult(
                        rule_id="RULE_CURRENCY_MISMATCH",
                        rule_name="Currency Consistency Check",
                        category="FINANCIAL",
                        result=RuleStatus.PASS,
                        severity=RuleSeverity.INFO,
                        expected_value=po.currency if po else "USD",
                        actual_value=invoice.currency or "USD",
                        explanation="Invoice currency matches Purchase Order currency."
                    )
                )

            # PO Balance Check (Remaining balance vs invoice total)
            if invoice.total_amount is not None:
                bal_tol = rules_config.financial.PO_BALANCE_TOLERANCE_ABS
                overage = round(invoice.total_amount - po.remaining_balance, 2)

                if overage <= bal_tol:
                    results.append(
                        RuleResult(
                            rule_id="RULE_PO_BALANCE_CHECK",
                            rule_name="PO Remaining Balance Check",
                            category="BALANCE",
                            result=RuleStatus.PASS,
                            severity=RuleSeverity.INFO,
                            expected_value=f"<= {po.remaining_balance:.2f}",
                            actual_value=f"{invoice.total_amount:.2f}",
                            explanation=f"Invoice total (${invoice.total_amount:.2f}) is within available PO balance (${po.remaining_balance:.2f})."
                        )
                    )
                else:
                    results.append(
                        RuleResult(
                            rule_id="RULE_PO_BALANCE_EXCEEDED",
                            rule_name="PO Overbilling / Balance Exceeded",
                            category="BALANCE",
                            result=RuleStatus.FAIL,
                            severity=RuleSeverity.HARD,
                            expected_value=f"<= {po.remaining_balance:.2f}",
                            actual_value=f"{invoice.total_amount:.2f}",
                            difference=f"+{overage:.2f}",
                            explanation=(
                                f"PO Overbilling: Invoice amount (${invoice.total_amount:.2f}) exceeds "
                                f"available PO remaining balance (${po.remaining_balance:.2f}) by ${overage:.2f}."
                            )
                        )
                    )

        return results
