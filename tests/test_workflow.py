"""
Integration tests for complete workflow orchestration, database persistence, and human overrides.
"""
from pathlib import Path
import pytest
from app.config.settings import settings
from app.database.entities import Invoice, PurchaseOrder, AuditLog
from app.workflow.orchestrator import WorkflowOrchestrator


def test_workflow_clean_invoice_end_to_end(test_db_session, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    orchestrator = WorkflowOrchestrator(test_db_session)
    clean_file = settings.SAMPLE_DATA_DIR / "scenario_1_clean_invoice.pdf"

    with open(clean_file, "rb") as f:
        file_bytes = f.read()

    inv, run = orchestrator.process_invoice(
        file_bytes=file_bytes,
        original_filename=clean_file.name
    )

    assert inv.id is not None
    assert inv.final_decision == "APPROVED"
    assert inv.processing_status == "COMPLETED"
    assert run.execution_status == "SUCCESS"

    # Verify PO balance was updated atomically
    po = test_db_session.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-2026-001").first()
    # Total PO was 2500.00, invoice amount was 1350.00 -> remaining balance 1150.00
    assert po.billed_amount == 1350.00
    assert po.remaining_balance == 1150.00


def test_workflow_human_override(test_db_session):
    orchestrator = WorkflowOrchestrator(test_db_session)

    # Create an initial invoice in NEEDS_REVIEW
    inv = Invoice(
        original_filename="test.pdf",
        file_hash="dummy_hash_for_override",
        file_path="/tmp/test.pdf",
        invoice_number="INV-REV-01",
        total_amount=500.00,
        final_decision="NEEDS_REVIEW",
        processing_status="COMPLETED"
    )
    test_db_session.add(inv)
    test_db_session.commit()

    # Apply manual override
    updated = orchestrator.apply_manual_override(
        invoice_id=inv.id,
        new_decision="APPROVED",
        reason="Vendor and items verified manually via purchase department email.",
        actor="JANE_DOE_SENIOR_ANALYST"
    )

    assert updated.manual_override_decision == "APPROVED"
    assert updated.manual_override_by == "JANE_DOE_SENIOR_ANALYST"
    assert "verified manually" in updated.manual_override_reason
    # Original automated decision must remain intact
    assert updated.final_decision == "NEEDS_REVIEW"

    # Verify audit log was recorded
    audit_entry = test_db_session.query(AuditLog).filter(
        AuditLog.invoice_id == inv.id,
        AuditLog.action == "MANUAL_OVERRIDE"
    ).first()
    assert audit_entry is not None
    assert audit_entry.previous_status == "NEEDS_REVIEW"
    assert audit_entry.new_status == "APPROVED"
    assert audit_entry.actor == "JANE_DOE_SENIOR_ANALYST"


def test_manual_currency_confirmation_is_audited(test_db_session):
    inv = Invoice(
        original_filename="ambiguous-dollar.png",
        file_hash="currency_review_hash",
        file_path="/tmp/ambiguous-dollar.png",
        total_amount=100.0,
        currency=None,
        final_decision="NEEDS_REVIEW",
        processing_status="COMPLETED",
    )
    test_db_session.add(inv)
    test_db_session.commit()

    updated = WorkflowOrchestrator(test_db_session).apply_manual_override(
        invoice_id=inv.id,
        new_decision="NEEDS_REVIEW",
        reason="Confirmed currency against vendor remittance instructions.",
        actor="AP_REVIEWER",
        corrected_fields={"currency": "HKD"},
    )

    assert updated.currency == "HKD"
    audit = test_db_session.query(AuditLog).filter(
        AuditLog.invoice_id == inv.id,
        AuditLog.action == "MANUAL_OVERRIDE",
    ).one()
    import json
    details = json.loads(audit.details)
    assert details["changes"]["currency"] == {"before": None, "after": "HKD"}


def test_paid_date_persists_on_invoice_record(test_db_session):
    inv = Invoice(
        original_filename="paid-date.png",
        file_hash="paid_date_persistence_hash",
        file_path="/tmp/paid-date.png",
        invoice_number="INV-PAID-1",
        paid_date="2021-06-22",
        final_decision="NEEDS_REVIEW",
    )
    test_db_session.add(inv)
    test_db_session.commit()

    reloaded = test_db_session.query(Invoice).filter_by(id=inv.id).one()
    assert reloaded.paid_date == "2021-06-22"


def test_fuzzy_raster_invoice_end_to_end_extracts_dates_and_routes_ambiguity_to_review(
    test_db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "uploads")
    settings.STORAGE_DIR.mkdir(parents=True)
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    fixture = Path(__file__).parent / "fixtures" / "fuzzy_invoice_low_res.png"

    invoice, run = WorkflowOrchestrator(test_db_session).process_invoice(
        file_bytes=fixture.read_bytes(),
        original_filename=fixture.name,
    )

    assert run.execution_status == "SUCCESS"
    assert invoice.invoice_date == "2021-06-22"
    assert invoice.due_date == "2021-06-27"
    assert invoice.paid_date == "2021-06-22"
    assert invoice.subtotal == 1564.0
    assert invoice.total_amount == 1564.0
    assert invoice.currency is None
    assert invoice.final_decision == "NEEDS_REVIEW"
    import json
    extraction = json.loads(invoice.extracted_data)
    assert extraction["ocr_confidence"] >= 55.0
    assert extraction["ocr_field_confidences"]["total_amount"] >= 55.0
