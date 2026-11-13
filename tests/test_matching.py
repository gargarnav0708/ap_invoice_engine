"""
Unit tests for vendor matching, purchase order matching, and line-item matching.
"""
import pytest
from app.core.schemas import NormalizedInvoice, NormalizedLineItem
from app.processing.vendor_matcher import VendorMatcher
from app.processing.po_matcher import POMatcher
from app.processing.line_item_matcher import LineItemMatcher
from app.database.entities import PurchaseOrder


def test_vendor_matching_exact_tax_id(test_db_session):
    matcher = VendorMatcher(test_db_session)
    invoice = NormalizedInvoice(
        vendor_tax_id="US-TAX-1011",
        vendor_name="Different Name"
    )
    vendor, cands, score, status = matcher.match_vendor(invoice)
    assert vendor is not None
    assert vendor.legal_name == "Acme Industrial Supplies Inc."
    assert status == "CONFIDENT_MATCH"
    assert score == 1.0


def test_vendor_matching_fuzzy_name(test_db_session):
    matcher = VendorMatcher(test_db_session)
    invoice = NormalizedInvoice(
        vendor_name="Acme Industrial Supplies",
        normalized_vendor_name="acme industrial supplies"
    )
    vendor, cands, score, status = matcher.match_vendor(invoice)
    assert vendor is not None
    assert "Acme" in vendor.legal_name
    assert score >= 0.85


def test_vendor_matching_unregistered(test_db_session):
    matcher = VendorMatcher(test_db_session)
    invoice = NormalizedInvoice(
        vendor_name="Completely Unknown Random Enterprise XYZ",
        normalized_vendor_name="completely unknown random enterprise xyz"
    )
    vendor, cands, score, status = matcher.match_vendor(invoice)
    assert vendor is None
    assert status == "NO_MATCH"


def test_po_matching_exact(test_db_session):
    po_matcher = POMatcher(test_db_session)
    vendor_matcher = VendorMatcher(test_db_session)

    invoice = NormalizedInvoice(
        vendor_tax_id="US-TAX-1011",
        po_reference="PO-2026-001"
    )
    vendor, _, _, _ = vendor_matcher.match_vendor(invoice)
    po, cands, score, status = po_matcher.match_po(invoice, vendor)

    assert po is not None
    assert po.po_number == "PO-2026-001"
    assert status == "CONFIDENT_MATCH"
    assert score == 1.0


def test_po_matching_vendor_mismatch(test_db_session):
    po_matcher = POMatcher(test_db_session)
    vendor_matcher = VendorMatcher(test_db_session)

    # PO-2026-001 belongs to Acme (Tax ID: US-TAX-1011)
    # But invoice vendor is Apex (Tax ID: US-TAX-2022)
    invoice = NormalizedInvoice(
        vendor_tax_id="US-TAX-2022",
        po_reference="PO-2026-001"
    )
    vendor, _, _, _ = vendor_matcher.match_vendor(invoice)
    po, cands, score, status = po_matcher.match_po(invoice, vendor)

    assert status == "VENDOR_MISMATCH"


def test_line_item_matching(test_db_session):
    po = test_db_session.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-2026-001").first()
    matcher = LineItemMatcher()

    invoice = NormalizedInvoice(
        line_items=[
            NormalizedLineItem(
                description="Safety Helmets",
                quantity=20.0,
                unit_price=50.00,
                line_total=1000.00
            ),
            NormalizedLineItem(
                description="Non-existent Office Desk",
                quantity=1.0,
                unit_price=300.00,
                line_total=300.00
            )
        ]
    )

    matches = matcher.match_line_items(invoice, po)
    assert len(matches) == 2
    # Item 1 should match PO Line 1 (Industrial Safety Helmets)
    assert matches[0].po_line_number == 1
    assert matches[0].score > 0.60
    # Item 2 should be unmatched
    assert matches[1].po_line_id is None
