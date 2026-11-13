"""
Synthetic Invoice Generator using ReportLab.
Produces realistic PDF fixtures representing the 4 core AP test scenarios.
"""
from pathlib import Path
import shutil
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

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


if __name__ == "__main__":
    generate_all_scenarios()
