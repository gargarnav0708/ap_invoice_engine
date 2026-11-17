"""
Synthetic Invoice Generator using ReportLab.
Produces PDF/image fixtures representing the 8 core AP demo scenarios.
"""
from pathlib import Path
import shutil
import tempfile
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from PIL import Image, ImageFilter
import io
import pymupdf

from app.config.settings import settings
from app.utils.logging import get_logger

logger = get_logger("generate_test_invoices")


def build_pdf_invoice(
    file_path: Path,
    vendor_name: str,
    vendor_address: str,
    vendor_tax_id: str,
    invoice_number: str,
    invoice_date: str,
    due_date: str,
    po_reference: str,
    currency: str,
    line_items: list,
    subtotal: float,
    tax: float,
    total: float,
    notes: str = ""
):
    """Generates a professional tabular invoice PDF."""
    doc = SimpleDocTemplate(
        str(file_path),
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=10
    )
    bold_style = ParagraphStyle(
        "BoldText",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        fontName="Helvetica-Bold"
    )
    normal_style = ParagraphStyle(
        "NormalText",
        parent=styles["Normal"],
        fontSize=10,
        leading=14
    )

    story = []

    # Header section: Vendor Info & Invoice Title
    header_data = [
        [
            Paragraph(f"<b>{vendor_name}</b><br/>{vendor_address}<br/>Tax ID: {vendor_tax_id}", normal_style),
            Paragraph("<b>INVOICE</b>", title_style)
        ]
    ]
    header_table = Table(header_data, colWidths=[340, 200])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 15))

    # Metadata grid (Invoice #, Date, PO Ref, Due Date)
    meta_data = [
        [
            Paragraph(f"<b>Invoice Number:</b> {invoice_number}", normal_style),
            Paragraph(f"<b>Invoice Date:</b> {invoice_date}", normal_style)
        ],
        [
            Paragraph(f"<b>PO Reference:</b> {po_reference}", normal_style),
            Paragraph(f"<b>Payment Due:</b> {due_date}", normal_style)
        ]
    ]
    meta_table = Table(meta_data, colWidths=[270, 270])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"<b>Currency:</b> {currency}", normal_style))
    story.append(Spacer(1, 8))

    # Line Items Table
    curr_sym = "£" if currency == "GBP" else ("€" if currency == "EUR" else "$")
    table_rows = [
        [
            Paragraph("<b>Description</b>", bold_style),
            Paragraph("<b>Qty</b>", bold_style),
            Paragraph("<b>Unit Price</b>", bold_style),
            Paragraph("<b>Total</b>", bold_style)
        ]
    ]

    for item in line_items:
        desc = item["desc"]
        qty = item["qty"]
        price = item["price"]
        ltotal = item["total"]
        table_rows.append([
            Paragraph(desc, normal_style),
            Paragraph(str(qty), normal_style),
            Paragraph(f"{curr_sym}{price:.2f}", normal_style),
            Paragraph(f"{curr_sym}{ltotal:.2f}", normal_style)
        ])

    items_table = Table(table_rows, colWidths=[310, 50, 90, 90])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 15))

    # Financial Summary
    summary_data = [
        ["", Paragraph("<b>Subtotal:</b>", normal_style), Paragraph(f"{curr_sym}{subtotal:.2f}", normal_style)],
        ["", Paragraph("<b>Tax:</b>", normal_style), Paragraph(f"{curr_sym}{tax:.2f}", normal_style)],
        ["", Paragraph("<b>Total Amount:</b>", bold_style), Paragraph(f"<b>{curr_sym}{total:.2f}</b>", bold_style)],
    ]
    summary_table = Table(summary_data, colWidths=[310, 140, 90])
    summary_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (1, 2), (2, 2), 1, colors.HexColor("#2B6CB0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(summary_table)

    if notes:
        story.append(Spacer(1, 25))
        story.append(Paragraph(f"<b>Notes:</b> {notes}", normal_style))

    doc.build(story)
    logger.info(f"Generated PDF invoice: {file_path.name}")


def generate_all_scenarios():
    settings.ensure_directories()
    dest = settings.SAMPLE_DATA_DIR

    # Scenario 1: Clean Invoice (APPROVED)
    s1_path = dest / "scenario_1_clean_invoice.pdf"
    build_pdf_invoice(
        file_path=s1_path,
        vendor_name="Acme Industrial Supplies Inc.",
        vendor_address="100 Industrial Parkway, Chicago, IL 60601",
        vendor_tax_id="US-TAX-1011",
        invoice_number="INV-2026-001",
        invoice_date="2026-02-15",
        due_date="2026-03-15",
        po_reference="PO-2026-001",
        currency="USD",
        line_items=[
            {"desc": "Industrial Safety Helmets", "qty": 20.0, "price": 50.00, "total": 1000.00},
            {"desc": "Heavy Duty Work Gloves", "qty": 10.0, "price": 25.00, "total": 250.00},
        ],
        subtotal=1250.00,
        tax=100.00,
        total=1350.00,
        notes="Standard terms: Net 30. Thank you for your business."
    )

    # Scenario 2: Duplicate Invoice (BLOCKED - same file hash / invoice number)
    s2_path = dest / "scenario_2_duplicate_invoice.pdf"
    shutil.copyfile(s1_path, s2_path)
    logger.info(f"Generated duplicate invoice fixture: {s2_path.name}")

    # Scenario 3: Split PO Overbilling (BLOCKED / Balance Exceeded)
    # PO-2026-002 has total $5,000, billed $4,500, remaining balance $500.
    # This invoice bills $1,100 ($1,000 + $100 tax), exceeding remaining balance by $600!
    s3_path = dest / "scenario_3_split_overbilling.pdf"
    build_pdf_invoice(
        file_path=s3_path,
        vendor_name="Apex Cloud Services LLC",
        vendor_address="500 Silicon Ave, San Jose, CA 95110",
        vendor_tax_id="US-TAX-2022",
        invoice_number="INV-2026-003",
        invoice_date="2026-02-20",
        due_date="2026-03-20",
        po_reference="PO-2026-002",
        currency="USD",
        line_items=[
            {"desc": "Cloud Hosting Dedicated Instances", "qty": 2.0, "price": 500.00, "total": 1000.00},
        ],
        subtotal=1000.00,
        tax=100.00,
        total=1100.00,
        notes="Billing period: Feb 2026. Cumulative PO overbilling test fixture."
    )

    # Scenario 4: Poor-Quality / Incomplete Invoice (NEEDS_REVIEW)
    # Missing invoice number, missing PO reference, unreadable tax calculation
    s4_path = dest / "scenario_4_incomplete_invoice.pdf"
    doc = SimpleDocTemplate(str(s4_path), pagesize=letter)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("<b>PRO-FORMA MEMORANDUM</b>", styles["Heading2"]),
        Spacer(1, 10),
        Paragraph("Vendor: Acme Industrial Supplies Inc.", styles["Normal"]),
        Paragraph("Date: 2026-02-25", styles["Normal"]),
        Spacer(1, 10),
        Paragraph("Notice: Missing formal Invoice Number and PO Reference.", styles["Normal"]),
        Paragraph("Item: Safety Supplies Batch - Total Amount: $450.00", styles["Normal"]),
        Paragraph("Tax breakdown: [ILLEGIBLE / UNSTATED]", styles["Normal"]),
    ]
    doc.build(story)
    logger.info(f"Generated incomplete invoice fixture: {s4_path.name}")

    # Scenario 5: Low-quality raster scan of the incomplete invoice (NEEDS_REVIEW).
    # Render at low resolution and enlarge with mild blur to exercise the OCR path.
    s5_path = dest / "scenario_5_scanned_low_quality.png"
    with pymupdf.open(str(s4_path)) as source_pdf:
        pixmap = source_pdf[0].get_pixmap(dpi=65, colorspace=pymupdf.csGRAY)
    scanned = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
    enlarged = scanned.resize(
        (scanned.width * 2, scanned.height * 2),
        resample=Image.Resampling.BILINEAR,
    ).filter(ImageFilter.GaussianBlur(radius=0.35))
    enlarged.save(s5_path, format="PNG", optimize=True)
    logger.info(f"Generated low-quality scanned invoice fixture: {s5_path.name}")

    # Scenario 6: Known vendor, no PO reference; multiple open Acme POs require review.
    build_pdf_invoice(
        file_path=dest / "scenario_6_ambiguous_po.pdf",
        vendor_name="Acme Industrial Supplies",
        vendor_address="100 Industrial Parkway, Chicago, IL 60601",
        vendor_tax_id="",
        invoice_number="INV-2026-006",
        invoice_date="2026-02-24",
        due_date="2026-03-24",
        po_reference="",
        currency="USD",
        line_items=[{"desc": "Industrial Safety Helmets", "qty": 1.0, "price": 50.00, "total": 50.00}],
        subtotal=50.00,
        tax=4.00,
        total=54.00,
        notes="PO reference omitted; reviewer must select the correct open order.",
    )

    # Scenario 7: Different PDF and invoice number, same vendor/date window/amount.
    # Run after Scenario 1 to demonstrate a potential near-duplicate review.
    build_pdf_invoice(
        file_path=dest / "scenario_7_changed_pdf_near_duplicate.pdf",
        vendor_name="Acme Industrial Supplies Inc.",
        vendor_address="100 Industrial Parkway, Chicago, IL 60601",
        vendor_tax_id="US-TAX-1011",
        invoice_number="INV-2026-001-R",
        invoice_date="2026-02-17",
        due_date="2026-03-17",
        po_reference="PO-2026-009",
        currency="USD",
        line_items=[
            {"desc": "Industrial Safety Helmets", "qty": 20.0, "price": 50.00, "total": 1000.00},
            {"desc": "Heavy Duty Work Gloves", "qty": 10.0, "price": 25.00, "total": 250.00},
        ],
        subtotal=1250.00,
        tax=100.00,
        total=1350.00,
        notes="Changed layout and invoice number; review for a near-duplicate of INV-2026-001.",
    )

    # Scenario 8: Sub-cent-scale arithmetic tolerance and within-policy PO price variance.
    build_pdf_invoice(
        file_path=dest / "scenario_8_small_tolerance_variance.pdf",
        vendor_name="Acme Industrial Supplies Inc.",
        vendor_address="100 Industrial Parkway, Chicago, IL 60601",
        vendor_tax_id="US-TAX-1011",
        invoice_number="INV-2026-008",
        invoice_date="2026-02-26",
        due_date="2026-03-26",
        po_reference="PO-2026-001",
        currency="USD",
        line_items=[{"desc": "Industrial Safety Helmets", "qty": 1.0, "price": 50.25, "total": 50.25}],
        subtotal=50.25,
        tax=4.04,
        total=54.33,
        notes="Declared total differs from subtotal plus tax by $0.04, within configured tolerance.",
    )


def ensure_scenario_fixtures():
    """Create missing demo fixtures without rewriting files already in use."""
    target_dir = settings.SAMPLE_DATA_DIR
    expected = [
        "scenario_1_clean_invoice.pdf",
        "scenario_2_duplicate_invoice.pdf",
        "scenario_3_split_overbilling.pdf",
        "scenario_4_incomplete_invoice.pdf",
        "scenario_5_scanned_low_quality.png",
        "scenario_6_ambiguous_po.pdf",
        "scenario_7_changed_pdf_near_duplicate.pdf",
        "scenario_8_small_tolerance_variance.pdf",
    ]
    missing = [name for name in expected if not (target_dir / name).exists()]
    if not missing:
        return

    original_dir = settings.SAMPLE_DATA_DIR
    try:
        with tempfile.TemporaryDirectory(prefix="ap-invoice-fixtures-") as temp_dir:
            settings.SAMPLE_DATA_DIR = Path(temp_dir)
            generate_all_scenarios()
            settings.SAMPLE_DATA_DIR = original_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            for name in missing:
                shutil.copy2(Path(temp_dir) / name, target_dir / name)
    finally:
        settings.SAMPLE_DATA_DIR = original_dir


if __name__ == "__main__":
    generate_all_scenarios()
