"""
Unit tests for financial calculations, arithmetic tolerances, and PO balance validation.
"""
import pytest
from app.core.schemas import NormalizedInvoice, NormalizedLineItem, RuleStatus, RuleSeverity
from app.database.entities import PurchaseOrder
from app.processing.financial_validator import FinancialValidator


def test_financial_math_pass(test_db_session):
    validator = FinancialValidator()
    po = test_db_session.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-2026-001").first()

    invoice = NormalizedInvoice(
        subtotal=1250.00,
        tax_amount=100.00,
        total_amount=1350.00,
        currency="USD",
        line_items=[
            NormalizedLineItem(description="Helmets", quantity=20.0, unit_price=50.00, line_total=1000.00),
            NormalizedLineItem(description="Gloves", quantity=10.0, unit_price=25.00, line_total=250.00)
        ]
    )

    results = validator.validate(invoice, po)
    failures = [r for r in results if r.result == RuleStatus.FAIL]
    assert len(failures) == 0


def test_financial_math_line_item_mismatch():
    validator = FinancialValidator()
    invoice = NormalizedInvoice(
        subtotal=100.00,
        tax_amount=0.00,
        total_amount=100.00,
        currency="USD",
        line_items=[
            # 10 * 5.00 should be 50.00, but declared as 80.00
            NormalizedLineItem(description="Bad Line Item", quantity=10.0, unit_price=5.00, line_total=80.00)
        ]
    )
    results = validator.validate(invoice, po=None)
    line_fails = [r for r in results if "RULE_LINE_MATH" in r.rule_id and r.result == RuleStatus.FAIL]
    assert len(line_fails) == 1
    assert line_fails[0].severity == RuleSeverity.HARD


def test_po_balance_overbilling(test_db_session):
    validator = FinancialValidator()
    # PO-2026-002 has total $5,000, billed $4,500, remaining balance $500.00
    po = test_db_session.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-2026-002").first()
    assert po.remaining_balance == 500.00

    invoice = NormalizedInvoice(
        subtotal=1000.00,
        tax_amount=100.00,
        total_amount=1100.00,  # $1,100 exceeds $500 remaining balance!
        currency="USD"
    )

    results = validator.validate(invoice, po)
    balance_fails = [r for r in results if r.rule_id == "RULE_PO_BALANCE_EXCEEDED"]
    assert len(balance_fails) == 1
    assert balance_fails[0].severity == RuleSeverity.HARD
    assert "exceeds" in balance_fails[0].explanation


def test_currency_mismatch(test_db_session):
    validator = FinancialValidator()
    # PO-2026-001 is USD
    po = test_db_session.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-2026-001").first()

    invoice = NormalizedInvoice(
        subtotal=100.00,
        tax_amount=0.00,
        total_amount=100.00,
        currency="EUR"  # Invoice in EUR, PO in USD
    )
    results = validator.validate(invoice, po)
    curr_fails = [r for r in results if r.rule_id == "RULE_CURRENCY_MISMATCH" and r.result == RuleStatus.FAIL]
    assert len(curr_fails) == 1
    assert curr_fails[0].severity == RuleSeverity.HARD


def test_total_that_conflicts_with_line_items_is_hard_failure():
    invoice = NormalizedInvoice(
        subtotal=8156400.0,
        tax_amount=0.0,
        total_amount=8156400.0,
        currency=None,
        line_items=[
            NormalizedLineItem(description="Furniture", quantity=1, unit_price=232, line_total=232),
            NormalizedLineItem(description="Services", quantity=2, unit_price=514, line_total=1028),
            NormalizedLineItem(description="Repair", quantity=2, unit_price=152, line_total=304),
        ],
    )
    results = FinancialValidator().validate(invoice)
    line_sum_check = next(result for result in results if result.rule_id == "RULE_TOTAL_LINE_SUM_CHECK")
    currency_check = next(result for result in results if result.rule_id == "RULE_CURRENCY_UNCONFIRMED")
    assert line_sum_check.result == RuleStatus.FAIL
    assert line_sum_check.severity == RuleSeverity.HARD
    assert line_sum_check.expected_value == "1564.00"
    assert currency_check.result == RuleStatus.WARN


def test_unconfirmed_currency_is_never_treated_as_usd():
    invoice = NormalizedInvoice(total_amount=100.0, currency=None)
    results = FinancialValidator().validate(invoice)
    warning = next(result for result in results if result.rule_id == "RULE_CURRENCY_UNCONFIRMED")
    assert warning.result == RuleStatus.WARN
