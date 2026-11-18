"""
Deterministic Financial Validator enforcing AP business rules, calculations, and PO balances.
"""
from datetime import date
from typing import List, Optional
from app.database.entities import PurchaseOrder
from app.core.schemas import NormalizedInvoice, RuleResult, RuleStatus, RuleSeverity
from app.config.rules import rules_config
from app.processing.currency import (
    SUPPORTED_CURRENCIES,
    convert_amount,
    exchange_rate,
)
from app.utils.logging import get_logger

logger = get_logger("financial_validator")


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


class FinancialValidator:
    def validate(
        self,
        invoice: NormalizedInvoice,
        po: Optional[PurchaseOrder] = None,
        allow_fx_conversion: bool = False,
    ) -> List[RuleResult]:
        """
        Runs comprehensive financial validation checks against an invoice and matched PO.
        """
        results: List[RuleResult] = []
        tol_abs = rules_config.financial.ARITHMETIC_TOLERANCE_ABS

        if invoice.currency is None:
            if po is not None and po.currency:
                invoice.currency = po.currency.strip().upper()
                results.append(
                    RuleResult(
                        rule_id="RULE_CURRENCY_CONFIRMED_FROM_PO",
                        rule_name="Invoice Currency Confirmed via PO",
                        category="FINANCIAL",
                        result=RuleStatus.PASS,
                        severity=RuleSeverity.INFO,
                        expected_value=po.currency,
                        actual_value=invoice.currency,
                        explanation=(
                            f"Invoice currency was confirmed as {invoice.currency} from matched "
                            f"Purchase Order {po.po_number}."
                        ),
                    )
                )
            else:
                results.append(
                    RuleResult(
                        rule_id="RULE_CURRENCY_UNCONFIRMED",
                        rule_name="Invoice Currency Confirmation",
                        category="FINANCIAL",
                        result=RuleStatus.WARN,
                        severity=RuleSeverity.SOFT,
                        expected_value="An explicit, unambiguous currency code",
                        actual_value="Unknown or ambiguous currency",
                        explanation=(
                            "The invoice does not identify a unique currency code. A bare '$' is ambiguous; "
                            "confirm the currency against the purchase order or vendor record before approval."
                        ),
                    )
                )

        # 0. Chronological date check: due_date must not precede invoice_date
        inv_dt = _parse_iso_date(invoice.invoice_date)
        due_dt = _parse_iso_date(invoice.due_date)
        if inv_dt is not None and due_dt is not None:
            if due_dt < inv_dt:
                results.append(
                    RuleResult(
                        rule_id="RULE_DUE_DATE_CHRONOLOGY",
                        rule_name="Due Date Chronology Check",
                        category="FINANCIAL",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.SOFT,
                        expected_value=f">= {inv_dt.isoformat()}",
                        actual_value=due_dt.isoformat(),
                        explanation=(
                            f"Payment due date ({due_dt.isoformat()}) is earlier than the invoice date "
                            f"({inv_dt.isoformat()}). Verify invoice dates before approval."
                        ),
                    )
                )
            else:
                results.append(
                    RuleResult(
                        rule_id="RULE_DUE_DATE_CHRONOLOGY",
                        rule_name="Due Date Chronology Check",
                        category="FINANCIAL",
                        result=RuleStatus.PASS,
                        severity=RuleSeverity.INFO,
                        expected_value=f">= {inv_dt.isoformat()}",
                        actual_value=due_dt.isoformat(),
                        explanation="Payment due date is on or after the invoice date.",
                    )
                )

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

            if invoice.line_items:
                line_sum = round(sum(item.line_total for item in invoice.line_items), 2)
                line_expected_total = round(line_sum + (invoice.tax_amount or 0.0), 2)
                line_total_diff = abs(invoice.total_amount - line_expected_total)
                if line_total_diff <= tol_abs:
                    results.append(RuleResult(
                        rule_id="RULE_TOTAL_LINE_SUM_CHECK",
                        rule_name="Total Matches Line Items and Tax",
                        category="FINANCIAL",
                        result=RuleStatus.PASS,
                        severity=RuleSeverity.INFO,
                        expected_value=f"{line_expected_total:.2f}",
                        actual_value=f"{invoice.total_amount:.2f}",
                        difference=f"{line_total_diff:.2f}",
                        explanation="Invoice total matches the sum of line items plus tax.",
                    ))
                else:
                    results.append(RuleResult(
                        rule_id="RULE_TOTAL_LINE_SUM_CHECK",
                        rule_name="Total Matches Line Items and Tax",
                        category="FINANCIAL",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value=f"{line_expected_total:.2f}",
                        actual_value=f"{invoice.total_amount:.2f}",
                        difference=f"{line_total_diff:.2f}",
                        explanation=(
                            f"Declared total ({invoice.total_amount:.2f}) does not match line items "
                            f"plus tax ({line_expected_total:.2f}). Review OCR and extracted amounts."
                        ),
                    ))

        # 4. PO Balance & Split Invoice Validation
        if po:
            inv_curr = (invoice.currency or "").upper()
            po_curr = (po.currency or "").upper()
            can_convert = (
                inv_curr in SUPPORTED_CURRENCIES
                and po_curr in SUPPORTED_CURRENCIES
                and (allow_fx_conversion or {inv_curr, po_curr} <= {"USD", "INR"})
            )

            # Currency check / FX conversion
            if inv_curr and po_curr and inv_curr != po_curr:
                if can_convert:
                    fx_rate = exchange_rate(inv_curr, po_curr)
                    conv_amt = (
                        convert_amount(invoice.total_amount, inv_curr, po_curr)
                        if invoice.total_amount is not None
                        else 0.0
                    )
                    results.append(
                        RuleResult(
                            rule_id="RULE_CURRENCY_CONVERTED",
                            rule_name="Cross-Currency FX Conversion",
                            category="FINANCIAL",
                            result=RuleStatus.PASS,
                            severity=RuleSeverity.INFO,
                            expected_value=po_curr,
                            actual_value=(
                                f"{inv_curr} {invoice.total_amount:,.2f} -> {po_curr} {conv_amt:,.2f}"
                                if invoice.total_amount is not None
                                else f"{inv_curr} -> {po_curr}"
                            ),
                            explanation=(
                                f"Converted invoice from {inv_curr} to PO currency {po_curr} "
                                f"at rate 1 {inv_curr} = {fx_rate:.4f} {po_curr}."
                            ),
                        )
                    )
                else:
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
            elif inv_curr and po_curr:
                results.append(
                    RuleResult(
                        rule_id="RULE_CURRENCY_MISMATCH",
                        rule_name="Currency Consistency Check",
                        category="FINANCIAL",
                        result=RuleStatus.PASS,
                        severity=RuleSeverity.INFO,
                        expected_value=po.currency,
                        actual_value=invoice.currency,
                        explanation="Invoice currency matches Purchase Order currency."
                    )
                )

            # PO Balance Check (Remaining balance vs invoice total in PO currency)
            if invoice.total_amount is not None:
                bal_tol = rules_config.financial.PO_BALANCE_TOLERANCE_ABS
                effective_po_amount = (
                    convert_amount(invoice.total_amount, inv_curr, po_curr)
                    if inv_curr and po_curr and inv_curr != po_curr and can_convert
                    else invoice.total_amount
                )
                overage = round(effective_po_amount - po.remaining_balance, 2)

                if overage <= bal_tol:
                    results.append(
                        RuleResult(
                            rule_id="RULE_PO_BALANCE_CHECK",
                            rule_name="PO Remaining Balance Check",
                            category="BALANCE",
                            result=RuleStatus.PASS,
                            severity=RuleSeverity.INFO,
                            expected_value=f"<= {po.currency} {po.remaining_balance:.2f}",
                            actual_value=f"{po.currency} {effective_po_amount:.2f}",
                            explanation=(
                                f"Invoice total ({po.currency} {effective_po_amount:.2f}) is within "
                                f"available PO balance ({po.currency} {po.remaining_balance:.2f})."
                            ),
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
                            expected_value=f"<= {po.currency} {po.remaining_balance:.2f}",
                            actual_value=f"{po.currency} {effective_po_amount:.2f}",
                            difference=f"+{overage:.2f}",
                            explanation=(
                                f"PO Overbilling: Invoice amount ({po.currency} {effective_po_amount:.2f}) exceeds "
                                f"available PO remaining balance ({po.currency} {po.remaining_balance:.2f}) by {po.currency} {overage:.2f}."
                            ),
                        )
                    )

        return results
