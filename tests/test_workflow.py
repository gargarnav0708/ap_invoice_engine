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
