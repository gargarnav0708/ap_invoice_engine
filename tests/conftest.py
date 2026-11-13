"""
Pytest configuration and shared test fixtures.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.entities import Base, Vendor, PurchaseOrder, POLineItem
from app.processing.normalizer import normalize_vendor_name


@pytest.fixture(scope="function")
def test_db_session():
    """In-memory SQLite database session isolated per test function."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    # Seed test vendors
    v1 = Vendor(
        legal_name="Acme Industrial Supplies Inc.",
        normalized_name=normalize_vendor_name("Acme Industrial Supplies Inc."),
        tax_id="US-TAX-1011",
        is_approved=True
    )
    v2 = Vendor(
        legal_name="Apex Cloud Services LLC",
        normalized_name=normalize_vendor_name("Apex Cloud Services LLC"),
        tax_id="US-TAX-2022",
        is_approved=True
    )
    v3 = Vendor(
        legal_name="Unapproved Suspicious Vendor Corp.",
        normalized_name=normalize_vendor_name("Unapproved Suspicious Vendor Corp."),
        tax_id="US-TAX-9999",
        is_approved=False
    )
    session.add_all([v1, v2, v3])
    session.flush()

    # Seed test POs
    po1 = PurchaseOrder(
        po_number="PO-2026-001",
        vendor_id=v1.id,
        po_date="2026-01-15",
        currency="USD",
        total_amount=2500.00,
        billed_amount=0.00,
        remaining_balance=2500.00,
        status="OPEN"
    )
    po2 = PurchaseOrder(
        po_number="PO-2026-002",
        vendor_id=v2.id,
        po_date="2026-01-10",
        currency="USD",
        total_amount=5000.00,
        billed_amount=4500.00,
        remaining_balance=500.00,
        status="OPEN"
    )
    po_closed = PurchaseOrder(
        po_number="PO-2026-CLOSED",
        vendor_id=v1.id,
        po_date="2025-01-01",
        currency="USD",
        total_amount=1000.00,
        billed_amount=1000.00,
        remaining_balance=0.00,
        status="CLOSED"
    )
    session.add_all([po1, po2, po_closed])
    session.flush()

    # Line items for PO 1
    li1 = POLineItem(po_id=po1.id, line_number=1, description="Industrial Safety Helmets", quantity=20.0, unit="EA", unit_price=50.00, line_total=1000.00)
    li2 = POLineItem(po_id=po1.id, line_number=2, description="Heavy Duty Work Gloves", quantity=10.0, unit="PAIR", unit_price=25.00, line_total=250.00)
    session.add_all([li1, li2])

    session.commit()

    try:
        yield session
    finally:
        session.close()
