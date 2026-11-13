"""
Unit tests for duplicate detection across all 4 levels.
"""
import pytest
from app.database.entities import Invoice
from app.core.schemas import NormalizedInvoice
from app.processing.duplicate_detector import DuplicateDetector


def test_level_1_exact_file_hash_duplicate(test_db_session):
    detector = DuplicateDetector(test_db_session)

    # Insert an existing invoice record
    existing = Invoice(
        original_filename="sample.pdf",
        file_hash="hash_1234567890abcdef",
        file_path="/tmp/sample.pdf",
        invoice_number="INV-100",
        total_amount=500.00
    )
    test_db_session.add(existing)
    test_db_session.commit()

    # Test identical hash
    res = detector.check_duplicate(
        file_hash="hash_1234567890abcdef",
        invoice=NormalizedInvoice(invoice_number="INV-999"),
        vendor_id=1
    )
    assert res.is_duplicate is True
    assert res.duplicate_type == "EXACT_HASH"
    assert res.severity == "CONFIRMED"
    assert res.matched_invoice_id == existing.id


def test_level_2_vendor_and_invoice_num_duplicate(test_db_session):
    detector = DuplicateDetector(test_db_session)

    existing = Invoice(
        original_filename="sample.pdf",
        file_hash="hash_original",
        file_path="/tmp/sample.pdf",
        vendor_id=1,
        invoice_number="INV-2026-X",
        total_amount=500.00
    )
    test_db_session.add(existing)
    test_db_session.commit()

    # Different hash, but same vendor ID and same invoice number
    res = detector.check_duplicate(
        file_hash="hash_different",
        invoice=NormalizedInvoice(invoice_number="INV-2026-X"),
        vendor_id=1
    )
    assert res.is_duplicate is True
    assert res.duplicate_type == "VENDOR_INVOICE_NUM"
    assert res.severity == "CONFIRMED"


def test_distinct_invoices_same_amount_not_blocked(test_db_session):
    detector = DuplicateDetector(test_db_session)

    existing = Invoice(
        original_filename="sample.pdf",
        file_hash="hash_original",
        file_path="/tmp/sample.pdf",
        vendor_id=1,
        invoice_number="INV-001",
        invoice_date="2026-01-01",
        total_amount=1500.00
    )
    test_db_session.add(existing)
    test_db_session.commit()

    # A completely distinct invoice with a different number, 45 days later, same amount
    res = detector.check_duplicate(
        file_hash="hash_completely_distinct",
        invoice=NormalizedInvoice(
            invoice_number="INV-002",
            invoice_date="2026-02-25",
            total_amount=1500.00
        ),
        vendor_id=1
    )
    assert res.is_duplicate is False
