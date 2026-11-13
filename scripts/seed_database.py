"""
Seed database script.
Populates realistic vendors, purchase orders, and line items.
"""
from datetime import datetime, date
from app.config.settings import settings
from app.database.db import init_db, get_db
from app.database.entities import Vendor, PurchaseOrder, POLineItem
from app.processing.normalizer import normalize_vendor_name
from app.utils.logging import get_logger

logger = get_logger("seed_database")


def seed():
    init_db()
    with get_db() as session:
        # Check if already seeded
        if session.query(Vendor).count() > 0:
            logger.info("Database already contains data. Skipping re-seeding.")
            return

        logger.info("Seeding database with realistic vendors and purchase orders...")

        # 1. Vendors
        vendors = [
            Vendor(
                legal_name="Acme Industrial Supplies Inc.",
                normalized_name=normalize_vendor_name("Acme Industrial Supplies Inc."),
                address="100 Industrial Parkway, Chicago, IL 60601",
                country="USA",
                tax_id="US-TAX-1011",
                is_approved=True,
                bank_account="US-BOA-88231"
            ),
            Vendor(
                legal_name="Apex Cloud Services LLC",
                normalized_name=normalize_vendor_name("Apex Cloud Services LLC"),
                address="500 Silicon Ave, San Jose, CA 95110",
                country="USA",
                tax_id="US-TAX-2022",
                is_approved=True,
                bank_account="US-WFC-44912"
            ),
            Vendor(
                legal_name="Global Office Logistics Ltd.",
                normalized_name=normalize_vendor_name("Global Office Logistics Ltd."),
                address="45 Thames Way, London, EC2A 1AB",
                country="UK",
                tax_id="UK-VAT-3033",
                is_approved=True,
                bank_account="GB-BARC-10928"
            ),
            Vendor(
                legal_name="TechNova Solutions Inc.",
                normalized_name=normalize_vendor_name("TechNova Solutions Inc."),
                address="120 Innovation Blvd, Austin, TX 78701",
                country="USA",
                tax_id="US-TAX-4044",
                is_approved=True,
                bank_account="US-CHASE-33910"
            ),
            Vendor(
                legal_name="Unverified Suspicious Vendor Corp.",
                normalized_name=normalize_vendor_name("Unverified Suspicious Vendor Corp."),
                address="99 Offshore Haven, Tortola, BVI",
                country="BVI",
                tax_id="US-TAX-9999",
                is_approved=False,  # Unapproved / Inactive
                bank_account="BVI-ANON-0001"
            ),
        ]
        session.add_all(vendors)
        session.flush()

        v_map = {v.legal_name: v for v in vendors}

        # 2. Purchase Orders
        pos = [
            # PO 1: Acme (Fresh, Clean, $2,500 total)
            PurchaseOrder(
                po_number="PO-2026-001",
                vendor_id=v_map["Acme Industrial Supplies Inc."].id,
                po_date="2026-01-15",
                expiry_date="2026-12-31",
                currency="USD",
                total_amount=2500.00,
                billed_amount=0.00,
                remaining_balance=2500.00,
                status="OPEN"
            ),
            # PO 2: Apex Cloud (Partially billed, remaining balance $500.00)
            PurchaseOrder(
                po_number="PO-2026-002",
                vendor_id=v_map["Apex Cloud Services LLC." if "Apex Cloud Services LLC." in v_map else "Apex Cloud Services LLC"].id,
                po_date="2026-01-10",
                expiry_date="2026-12-31",
                currency="USD",
                total_amount=5000.00,
                billed_amount=4500.00,
                remaining_balance=500.00,
                status="OPEN"
            ),
            # PO 3: Global Office Logistics (GBP currency, $1,800 total)
            PurchaseOrder(
                po_number="PO-2026-003",
                vendor_id=v_map["Global Office Logistics Ltd."].id,
                po_date="2026-02-01",
                expiry_date="2026-12-31",
                currency="GBP",
                total_amount=1800.00,
                billed_amount=0.00,
                remaining_balance=1800.00,
                status="OPEN"
            ),
            # PO 4: TechNova (USD $8,000, unbilled)
            PurchaseOrder(
                po_number="PO-2026-004",
                vendor_id=v_map["TechNova Solutions Inc."].id,
                po_date="2026-02-15",
                expiry_date="2026-12-31",
                currency="USD",
                total_amount=8000.00,
                billed_amount=0.00,
                remaining_balance=8000.00,
                status="OPEN"
            ),
            # PO 5: TechNova (CLOSED PO)
            PurchaseOrder(
                po_number="PO-2026-005",
                vendor_id=v_map["TechNova Solutions Inc."].id,
                po_date="2025-06-01",
                expiry_date="2025-12-31",
                currency="USD",
                total_amount=3200.00,
                billed_amount=3200.00,
                remaining_balance=0.00,
                status="CLOSED"
            ),
            # PO 6: Acme (EUR currency, €4,000 total)
            PurchaseOrder(
                po_number="PO-2026-006",
                vendor_id=v_map["Acme Industrial Supplies Inc."].id,
                po_date="2026-02-10",
                expiry_date="2026-12-31",
                currency="EUR",
                total_amount=4000.00,
                billed_amount=0.00,
                remaining_balance=4000.00,
                status="OPEN"
            ),
            # PO 7: Apex (OPEN, $1,500 total)
            PurchaseOrder(
                po_number="PO-2026-007",
                vendor_id=v_map["Apex Cloud Services LLC"].id,
                po_date="2026-02-18",
                expiry_date="2026-12-31",
                currency="USD",
                total_amount=1500.00,
                billed_amount=0.00,
                remaining_balance=1500.00,
                status="OPEN"
            ),
            # PO 8: Global Office Logistics (OPEN, GBP 900.00)
            PurchaseOrder(
                po_number="PO-2026-008",
                vendor_id=v_map["Global Office Logistics Ltd."].id,
                po_date="2026-02-20",
                expiry_date="2026-12-31",
                currency="GBP",
                total_amount=900.00,
                billed_amount=0.00,
                remaining_balance=900.00,
                status="OPEN"
            ),
            # PO 9: Acme ($10,000 total, partly billed $3,000)
            PurchaseOrder(
                po_number="PO-2026-009",
                vendor_id=v_map["Acme Industrial Supplies Inc."].id,
                po_date="2026-01-05",
                expiry_date="2026-12-31",
                currency="USD",
                total_amount=10000.00,
                billed_amount=3000.00,
                remaining_balance=7000.00,
                status="OPEN"
            ),
            # PO 10: Unapproved vendor PO (CANCELLED)
            PurchaseOrder(
                po_number="PO-2026-010",
                vendor_id=v_map["Unverified Suspicious Vendor Corp."].id,
                po_date="2026-01-01",
                expiry_date="2026-12-31",
                currency="USD",
                total_amount=5000.00,
                billed_amount=0.00,
                remaining_balance=5000.00,
                status="CANCELLED"
            ),
        ]
        session.add_all(pos)
        session.flush()

        po_map = {p.po_number: p for p in pos}

        # 3. PO Line Items
        line_items = [
            # PO-2026-001 line items
            POLineItem(po_id=po_map["PO-2026-001"].id, line_number=1, description="Industrial Safety Helmets", quantity=20.0, unit="EA", unit_price=50.00, line_total=1000.00),
            POLineItem(po_id=po_map["PO-2026-001"].id, line_number=2, description="Heavy Duty Work Gloves", quantity=10.0, unit="PAIR", unit_price=25.00, line_total=250.00),
            POLineItem(po_id=po_map["PO-2026-001"].id, line_number=3, description="High Visibility Vests", quantity=25.0, unit="EA", unit_price=50.00, line_total=1250.00),

            # PO-2026-002 line items
            POLineItem(po_id=po_map["PO-2026-002"].id, line_number=1, description="Cloud Hosting Dedicated Instances", quantity=10.0, unit="MONTH", unit_price=500.00, line_total=5000.00),

            # PO-2026-003 line items
            POLineItem(po_id=po_map["PO-2026-003"].id, line_number=1, description="Ergonomic Desk Chairs", quantity=12.0, unit="EA", unit_price=150.00, line_total=1800.00),

            # PO-2026-004 line items
            POLineItem(po_id=po_map["PO-2026-004"].id, line_number=1, description="Enterprise Security Monitoring License", quantity=1.0, unit="ANNUAL", unit_price=8000.00, line_total=8000.00),
        ]
        session.add_all(line_items)
        session.commit()

        logger.info(f"Database seeded successfully with {len(vendors)} vendors, {len(pos)} POs, and {len(line_items)} line items.")


if __name__ == "__main__":
    seed()
