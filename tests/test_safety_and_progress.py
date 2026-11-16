"""Regression coverage for safe reservations, scanned inputs, and live runs."""
import json
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.config.settings import settings
from app.core.schemas import DecisionStatus
from app.database.entities import Invoice, ProcessingRun, PurchaseOrder
from app.utils.file_storage import validate_document_bytes
from app.workflow.orchestrator import WorkflowOrchestrator


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_po_reservation_is_conditional_and_cent_exact(test_db_session):
    po = test_db_session.query(PurchaseOrder).filter_by(po_number="PO-2026-002").one()
    repo = WorkflowOrchestrator(test_db_session).po_repo

    ok, error = repo.update_billed_balance_atomic(po.id, 500.00, "ADD")
    assert ok and error is None
    ok, error = repo.update_billed_balance_atomic(po.id, 0.01, "ADD")

    assert not ok
    assert "enough remaining balance" in error
    po = test_db_session.query(PurchaseOrder).filter_by(id=po.id).one()
    assert po.billed_amount == 5000.00
    assert po.remaining_balance == 0.00


def test_corrected_approved_invoice_moves_po_reservation(test_db_session):
    old_po = test_db_session.query(PurchaseOrder).filter_by(po_number="PO-2026-001").one()
    new_po = test_db_session.query(PurchaseOrder).filter_by(po_number="PO-2026-002").one()
    old_po.billed_amount = 1350.00
    old_po.remaining_balance = 1150.00
    invoice = Invoice(
        original_filename="approved.pdf",
        file_hash="a" * 64,
        file_path="approved.pdf",
        matched_po_id=old_po.id,
        invoice_number="INV-TEST-1",
        total_amount=1350.00,
        final_decision=DecisionStatus.APPROVED.value,
        processing_status="COMPLETED",
    )
    test_db_session.add(invoice)
    test_db_session.commit()

    WorkflowOrchestrator(test_db_session).apply_manual_override(
        invoice_id=invoice.id,
        new_decision=DecisionStatus.APPROVED.value,
        reason="Corrected PO reference and amount",
        corrected_fields={"matched_po_id": new_po.id, "total_amount": 100.00},
    )

    assert test_db_session.query(PurchaseOrder).filter_by(id=old_po.id).one().remaining_balance == 2500.00
    assert test_db_session.query(PurchaseOrder).filter_by(id=new_po.id).one().remaining_balance == 400.00


def test_run_stage_events_are_live_and_persisted(test_db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    events = []
    progress_snapshots = []

    def observe_progress(event):
        events.append(event)
        if event.get("stage_name") == "5. Extract document text" and event.get("type") == "start":
            run = test_db_session.query(ProcessingRun).order_by(ProcessingRun.id.desc()).first()
            progress_snapshots.append(json.loads(run.stages_log))

    invoice_bytes = (REPO_ROOT / "data/sample_data/scenario_1_clean_invoice.pdf").read_bytes()
    invoice, run = WorkflowOrchestrator(test_db_session).process_invoice(
        invoice_bytes, "scenario_1_clean_invoice.pdf", progress_callback=observe_progress
    )

    assert invoice.final_decision == DecisionStatus.APPROVED.value
    assert len(events) == 38
    assert len(json.loads(run.stages_log)) == 19
    assert len(progress_snapshots) == 1
    assert progress_snapshots[0][-1]["stage_name"] == "5. Extract document text"
    assert progress_snapshots[0][-1]["status"] == "RUNNING"


def test_invoice_is_blocked_if_po_balance_changes_before_reservation(
    test_db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    orchestrator = WorkflowOrchestrator(test_db_session)
    original_update = orchestrator.po_repo.update_billed_balance_atomic

    def simulate_another_approval(*, po_id, invoice_amount, action="ADD"):
        po = test_db_session.query(PurchaseOrder).filter_by(id=po_id).one()
        po.remaining_balance = 0.00
        test_db_session.flush()
        return original_update(po_id=po_id, invoice_amount=invoice_amount, action=action)

    orchestrator.po_repo.update_billed_balance_atomic = simulate_another_approval
    invoice_bytes = (REPO_ROOT / "data/sample_data/scenario_1_clean_invoice.pdf").read_bytes()
    invoice, run = orchestrator.process_invoice(invoice_bytes, "raced-clean-invoice.pdf")

    assert invoice.final_decision == DecisionStatus.BLOCKED.value
    assert run.final_decision == DecisionStatus.BLOCKED.value
    assert any(rule.rule_id == "RULE_PO_BALANCE_RESERVATION" for rule in invoice.validations)


def test_scanned_invoice_without_ocr_goes_to_review(test_db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "uploads")
    settings.STORAGE_DIR.mkdir()
    monkeypatch.setattr(settings, "OCR_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    image = Image.new("RGB", (1200, 1600), "white")
    ImageDraw.Draw(image).text((100, 100), "Invoice Total $100.00", fill="black")
    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    pdf_path = tmp_path / "scanned-invoice.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=(600, 800))
    pdf.drawImage(ImageReader(image_buffer), 0, 0, width=600, height=800)
    pdf.save()

    invoice, _run = WorkflowOrchestrator(test_db_session).process_invoice(
        pdf_path.read_bytes(), pdf_path.name
    )

    assert invoice.final_decision == DecisionStatus.NEEDS_REVIEW.value
    assert any(rule.rule_id == "RULE_OCR_UNAVAILABLE" for rule in invoice.validations)


def test_raster_image_invoice_is_accepted_and_routes_ocr_failure_to_review(
    test_db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "uploads")
    settings.STORAGE_DIR.mkdir()
    monkeypatch.setattr(settings, "OCR_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    image = Image.new("RGB", (1200, 1600), "white")
    ImageDraw.Draw(image).text((100, 100), "Invoice Total $100.00", fill="black")
    image_path = tmp_path / "invoice.png"
    image.save(image_path, format="PNG")
    image_bytes = image_path.read_bytes()
    assert validate_document_bytes(image_bytes, image_path.name) == "image"

    invoice, run = WorkflowOrchestrator(test_db_session).process_invoice(
        image_bytes, image_path.name
    )

    assert invoice.final_decision == DecisionStatus.NEEDS_REVIEW.value
    assert invoice.original_filename.endswith("invoice.png")
    assert any(rule.rule_id == "RULE_OCR_UNAVAILABLE" for rule in invoice.validations)
    assert "5. Extract document text" in run.stages_log


def test_raster_invoice_ocr_text_flows_through_deterministic_decision(
    test_db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "uploads")
    settings.STORAGE_DIR.mkdir()
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    class DemoOCR:
        def extract_text_from_image(self, _image):
            return """Vendor: Acme Industrial Supplies Inc.
Tax ID: US-TAX-1011
Invoice Number: INV-2026-888
Invoice Date: 2026-10-02
PO Reference: PO-2026-001
Currency: USD
Subtotal: $1,250.00
Tax (8%): $100.00
Total Amount: $1,350.00
ITEM: Industrial Safety Helmets | Qty: 20 | Price: $50.00 | Total: $1,000.00
ITEM: Heavy Duty Work Gloves | Qty: 10 | Price: $25.00 | Total: $250.00"""

    monkeypatch.setattr("app.ingestion.pdf_loader.get_ocr_engine", lambda: DemoOCR())
    image = Image.new("RGB", (1200, 1600), "white")
    image_path = tmp_path / "ocr-invoice.jpg"
    image.save(image_path, format="JPEG")

    invoice, run = WorkflowOrchestrator(test_db_session).process_invoice(
        image_path.read_bytes(), image_path.name
    )

    assert invoice.final_decision == DecisionStatus.APPROVED.value
    assert "INV-2026-888" in invoice.extracted_data
    assert run.final_decision == DecisionStatus.APPROVED.value
