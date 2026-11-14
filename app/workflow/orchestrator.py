"""
Central Workflow Orchestrator coordinating all 19 processing stages for AP invoices.
Supports atomic transactions, idempotent reruns, and human overrides.
"""
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Callable
from datetime import datetime, timezone
import time
import json
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.exceptions import APException, IngestionError, WorkflowError
from app.core.schemas import (
    ExtractedInvoice, NormalizedInvoice, DecisionStatus, RuleStatus,
    RuleResult, LineItemMatch
)
from app.database.entities import (
    Invoice, InvoiceLineItem, ProcessingRun, ValidationResultEntity,
    Vendor, PurchaseOrder
)
from app.database.repositories import (
    InvoiceRepository, VendorRepository, PORepository, AuditRepository
)
from app.utils.file_storage import save_uploaded_file, validate_pdf_bytes
from app.utils.hashing import calculate_file_hash
from app.ingestion.pdf_loader import PDFLoader
from app.extraction.extractor import InvoiceExtractor
from app.processing.normalizer import normalize_invoice
from app.processing.vendor_matcher import VendorMatcher
from app.processing.po_matcher import POMatcher
from app.processing.duplicate_detector import DuplicateDetector, DuplicateCheckResult
from app.processing.line_item_matcher import LineItemMatcher
from app.decision.rules_engine import RulesEngine
from app.decision.decision_service import DecisionService
from app.workflow.execution_logger import ExecutionLogger
from app.utils.logging import get_logger

logger = get_logger("orchestrator")


class WorkflowOrchestrator:
    def __init__(self, session: Session):
        self.session = session
        self.inv_repo = InvoiceRepository(session)
        self.vendor_repo = VendorRepository(session)
        self.po_repo = PORepository(session)
        self.audit_repo = AuditRepository(session)

        self.pdf_loader = PDFLoader()
        self.extractor = InvoiceExtractor()
        self.vendor_matcher = VendorMatcher(session)
        self.po_matcher = POMatcher(session)
        self.duplicate_detector = DuplicateDetector(session)
        self.line_matcher = LineItemMatcher()
        self.rules_engine = RulesEngine()
        self.decision_service = DecisionService()

    def process_invoice(
        self,
        file_bytes: Optional[bytes] = None,
        original_filename: str = "invoice.pdf",
        existing_file_path: Optional[Path] = None,
        existing_invoice_id: Optional[int] = None,
        progress_callback: Optional[Callable[[dict], None]] = None
    ) -> Tuple[Invoice, ProcessingRun]:
        """
        Executes the end-to-end 19-stage AP invoice processing workflow.

        ``progress_callback`` is an optional function that receives a dict for
        every stage start / completion / failure, enabling a live run view.
        The resulting ``ProcessingRun`` carries the computed ``DecisionResult``
        on a transient ``decision_result`` attribute for the caller to render.
        """
        exec_logger = ExecutionLogger(on_event=progress_callback)
        start_time_perf = time.perf_counter()
        start_time_dt = datetime.now(timezone.utc)

        # Temporary placeholders before entity persistence
        invoice_entity: Optional[Invoice] = None
        run_entity: Optional[ProcessingRun] = None

        try:
            # Stage 1: Receive invoice
            exec_logger.start_stage("1. Receive invoice")
            if existing_file_path and existing_file_path.exists():
                file_path = existing_file_path
                exec_logger.complete_stage(f"Received existing invoice file: {file_path.name}")
            elif file_bytes:
                exec_logger.complete_stage(f"Received uploaded file: {original_filename} ({len(file_bytes)} bytes)")
            else:
                raise IngestionError("No file bytes or file path provided to orchestrator.")

            # Stage 2: Validate uploaded file
            exec_logger.start_stage("2. Validate uploaded file")
            if file_bytes:
                validate_pdf_bytes(file_bytes, original_filename)
                file_path, safe_filename = save_uploaded_file(file_bytes, original_filename)
            else:
                safe_filename = file_path.name
            exec_logger.complete_stage(f"Validated PDF header and size. Stored at {file_path.name}")

            # Stage 3: Calculate file hash
            exec_logger.start_stage("3. Calculate file hash")
            file_hash = calculate_file_hash(file_path)
            exec_logger.complete_stage(f"SHA-256: {file_hash}")

            # Initialize Invoice Entity in DB if not already present
            if existing_invoice_id:
                invoice_entity = self.inv_repo.get_by_id(existing_invoice_id)
            if not invoice_entity:
                invoice_entity = Invoice(
                    original_filename=safe_filename,
                    file_hash=file_hash,
                    file_path=str(file_path),
                    processing_status="PROCESSING",
                    final_decision=DecisionStatus.NEEDS_REVIEW.value
                )
                self.session.add(invoice_entity)
                self.session.flush()

            # Initialize Processing Run Entity
            run_entity = ProcessingRun(
                invoice_id=invoice_entity.id,
                start_time=start_time_dt,
                current_stage="INITIALIZING",
                execution_status="RUNNING"
            )
            self.session.add(run_entity)
            self.session.flush()

            self.audit_repo.log(
                action="WORKFLOW_STARTED",
                invoice_id=invoice_entity.id,
                run_id=run_entity.id,
                details=f"Started processing run for {safe_filename}."
            )

            # Stage 4: Check for exact duplicate
            exec_logger.start_stage("4. Check for exact duplicate")
            dup_check = self.duplicate_detector.check_duplicate(
                file_hash=file_hash,
                invoice=NormalizedInvoice(),
                vendor_id=None,
                current_invoice_id=invoice_entity.id
            )
            exact_duplicate_blocked = (dup_check.is_duplicate and dup_check.duplicate_type == "EXACT_HASH")
            exec_logger.complete_stage(
                f"Duplicate check: is_dup={dup_check.is_duplicate}, type={dup_check.duplicate_type}"
            )

            # Stage 5: Extract PDF text & Stage 6: Run OCR when necessary
            exec_logger.start_stage("5. Extract PDF text")
            ingestion_res = self.pdf_loader.load_pdf(file_path)
            exec_logger.complete_stage(f"Extracted {len(ingestion_res.full_text)} chars across {ingestion_res.total_pages} page(s).")

            exec_logger.start_stage("6. Run OCR when necessary")
            if ingestion_res.ocr_applied:
                exec_logger.complete_stage("OCR engine engaged for scanned/low-text content.")
            else:
                exec_logger.complete_stage("Machine-readable text sufficient. OCR bypassed.")

            # Stage 7: Extract structured invoice information
            exec_logger.start_stage("7. Extract structured invoice information")
            extracted_data = self.extractor.llm_provider.extract_invoice(ingestion_res.full_text)
            extracted_data.document_type = ingestion_res.document_type
            exec_logger.complete_stage(f"Extracted fields via {self.extractor.llm_provider.provider_name} (conf={extracted_data.overall_confidence:.2f})")

            # Stage 8: Validate extracted schema
            exec_logger.start_stage("8. Validate extracted schema")
            # Pydantic schema is validated upon model instantiation
            exec_logger.complete_stage("Structured schema validated successfully against Pydantic schema.")

            # Stage 9: Normalise extracted fields
            exec_logger.start_stage("9. Normalise extracted fields")
            normalized = normalize_invoice(extracted_data)
            exec_logger.complete_stage(
                f"Normalized vendor='{normalized.normalized_vendor_name}', inv_no='{normalized.normalized_invoice_number}', total={normalized.total_amount}"
            )

            # Stage 10: Match vendor
            exec_logger.start_stage("10. Match vendor")
            matched_vendor, vendor_cands, vendor_score, vendor_status = self.vendor_matcher.match_vendor(normalized)
            vendor_name_matched = matched_vendor.legal_name if matched_vendor else "None"
            exec_logger.complete_stage(f"Vendor match: '{vendor_name_matched}' (status={vendor_status}, score={vendor_score:.2f})")

            # Stage 11: Match purchase order
            exec_logger.start_stage("11. Match purchase order")
            matched_po, po_cands, po_score, po_status = self.po_matcher.match_po(normalized, matched_vendor)
            po_num_matched = matched_po.po_number if matched_po else "None"
            exec_logger.complete_stage(f"PO match: '{po_num_matched}' (status={po_status}, score={po_score:.2f})")

            # Stage 12: Detect duplicate invoices (Level 2-4 with vendor context)
            exec_logger.start_stage("12. Detect duplicate invoices")
            if not exact_duplicate_blocked:
                dup_check = self.duplicate_detector.check_duplicate(
                    file_hash=file_hash,
                    invoice=normalized,
                    vendor_id=matched_vendor.id if matched_vendor else None,
                    current_invoice_id=invoice_entity.id
                )
            exec_logger.complete_stage(f"Duplicate result: {dup_check.duplicate_type} ({dup_check.severity})")

            # Stage 13: Match line items
            exec_logger.start_stage("13. Match line items")
            line_item_matches = self.line_matcher.match_line_items(normalized, matched_po)
            exec_logger.complete_stage(f"Matched {len(line_item_matches)} line items to PO lines.")

            # Stage 14: Run financial validation (arithmetic, subtotal, tax, currency)
            exec_logger.start_stage("14. Run financial validation")
            financial_results = self.rules_engine.financial_validator.validate(normalized, matched_po)
            fin_fails = [r for r in financial_results
                         if r.category == "FINANCIAL" and r.result == RuleStatus.FAIL]
            fin_checks = [r for r in financial_results if r.category == "FINANCIAL"]
            if fin_fails:
                exec_logger.complete_stage(
                    f"{len(fin_checks)} financial check(s): {len(fin_fails)} failed — "
                    + "; ".join(r.rule_name for r in fin_fails)
                )
            else:
                exec_logger.complete_stage(
                    f"{len(fin_checks)} financial check(s) passed (arithmetic, subtotal, tax, currency)."
                )

            # Stage 15: Run PO balance validation (cumulative billing vs remaining balance)
            exec_logger.start_stage("15. Run PO balance validation")
            balance_results = [r for r in financial_results if r.category == "BALANCE"]
            balance_fail = next((r for r in balance_results if r.result == RuleStatus.FAIL), None)
            if balance_fail:
                exec_logger.complete_stage(f"PO balance check FAILED: {balance_fail.explanation}")
            elif balance_results:
                exec_logger.complete_stage(f"PO balance check passed: {balance_results[0].explanation}")
            else:
                exec_logger.complete_stage("No PO linked; balance check not applicable.")

            # Stage 16: Generate final decision
            exec_logger.start_stage("16. Generate final decision")
            validation_results = self.rules_engine.evaluate_all_rules(
                extracted=extracted_data,
                normalized=normalized,
                matched_vendor=matched_vendor,
                vendor_status=vendor_status,
                matched_po=matched_po,
                po_status=po_status,
                duplicate_check=dup_check,
                line_item_matches=line_item_matches,
                financial_results=financial_results
            )
            decision = self.decision_service.determine_decision(validation_results)
            exec_logger.complete_stage(f"Final Decision generated: {decision.status.value}")

            # Stage 17: Save all results
            exec_logger.start_stage("17. Save all results")
            invoice_entity.vendor_id = matched_vendor.id if matched_vendor else None
            invoice_entity.matched_po_id = matched_po.id if matched_po else None
            invoice_entity.invoice_number = normalized.invoice_number
            invoice_entity.invoice_date = normalized.invoice_date
            invoice_entity.due_date = normalized.due_date
            invoice_entity.po_reference = normalized.po_reference
            invoice_entity.currency = normalized.currency
            invoice_entity.subtotal = normalized.subtotal
            invoice_entity.tax_amount = normalized.tax_amount
            invoice_entity.total_amount = normalized.total_amount
            invoice_entity.extracted_data = extracted_data.model_dump_json()
            invoice_entity.extraction_confidence = extracted_data.overall_confidence
            invoice_entity.processing_status = "COMPLETED"
            invoice_entity.final_decision = decision.status.value

            # Save line items
            self.session.query(InvoiceLineItem).filter(InvoiceLineItem.invoice_id == invoice_entity.id).delete()
            for idx, item in enumerate(normalized.line_items):
                matched_po_line_id = line_item_matches[idx].po_line_id if idx < len(line_item_matches) else None
                inv_line = InvoiceLineItem(
                    invoice_id=invoice_entity.id,
                    line_number=idx + 1,
                    description=item.description,
                    quantity=item.quantity,
                    unit=item.unit,
                    unit_price=item.unit_price,
                    tax_amount=item.tax_amount,
                    line_total=item.line_total,
                    matched_po_line_id=matched_po_line_id
                )
                self.session.add(inv_line)

            # Save validation results
            self.session.query(ValidationResultEntity).filter(ValidationResultEntity.invoice_id == invoice_entity.id).delete()
            for r in validation_results:
                v_entity = ValidationResultEntity(
                    invoice_id=invoice_entity.id,
                    run_id=run_entity.id,
                    rule_id=r.rule_id,
                    rule_name=r.rule_name,
                    category=r.category,
                    result=r.result.value,
                    severity=r.severity.value,
                    expected_value=r.expected_value,
                    actual_value=r.actual_value,
                    difference=r.difference,
                    explanation=r.explanation,
                    evidence=r.evidence
                )
                self.session.add(v_entity)
            exec_logger.complete_stage("Saved invoice, line items, and validation records.")

            # Stage 18: Update invoice and PO records as appropriate
            exec_logger.start_stage("18. Update invoice and PO records")
            # If the invoice is AUTOMATICALLY APPROVED, atomically update PO billed balance
            if decision.status == DecisionStatus.APPROVED and matched_po and normalized.total_amount:
                success, err = self.po_repo.update_billed_balance_atomic(
                    po_id=matched_po.id,
                    invoice_amount=normalized.total_amount,
                    action="ADD"
                )
                if success:
                    exec_logger.complete_stage(
                        f"Updated PO {matched_po.po_number}: Billed +${normalized.total_amount:.2f}, Rem. Balance ${matched_po.remaining_balance:.2f}"
                    )
                else:
                    exec_logger.complete_stage(f"PO balance update failed: {err}")
            else:
                exec_logger.complete_stage("PO balance unchanged (Invoice not approved or no PO linked).")

            # Stage 19: Generate the final audit report
            exec_logger.start_stage("19. Generate the final audit report")
            self.audit_repo.log(
                action="DECISION_GENERATED",
                invoice_id=invoice_entity.id,
                run_id=run_entity.id,
                details=f"Decision: {decision.status.value}. Summary: {decision.summary}",
                previous_status="PROCESSING",
                new_status=decision.status.value,
                actor="SYSTEM_RULES_ENGINE"
            )
            exec_logger.complete_stage("Audit log entry successfully created.")

            # Finalize run entity
            duration_total_ms = (time.perf_counter() - start_time_perf) * 1000.0
            run_entity.completion_time = datetime.now(timezone.utc)
            run_entity.current_stage = "COMPLETED"
            run_entity.execution_status = "SUCCESS"
            run_entity.final_decision = decision.status.value
            run_entity.duration_ms = round(duration_total_ms, 2)
            run_entity.stages_log = exec_logger.to_json()

            self.session.commit()
            # Transient attribute for the caller's live decision card (not persisted).
            run_entity.decision_result = decision
            logger.info(f"Processing run completed for invoice {invoice_entity.id} in {duration_total_ms:.1f}ms with decision {decision.status.value}")
            return invoice_entity, run_entity

        except Exception as e:
            self.session.rollback()
            logger.error(f"Processing error in workflow: {e}", exc_info=True)
            duration_total_ms = (time.perf_counter() - start_time_perf) * 1000.0
            exec_logger.fail_stage(str(e))

            if invoice_entity:
                invoice_entity.processing_status = "FAILED"
                invoice_entity.final_decision = DecisionStatus.NEEDS_REVIEW.value
            if run_entity:
                run_entity.completion_time = datetime.now(timezone.utc)
                run_entity.current_stage = "FAILED"
                run_entity.execution_status = "FAILED"
                run_entity.error_message = str(e)
                run_entity.duration_ms = round(duration_total_ms, 2)
                run_entity.stages_log = exec_logger.to_json()

            try:
                self.session.commit()
            except Exception:
                pass

            raise WorkflowError(f"Workflow execution failed: {str(e)}")

    def apply_manual_override(
        self,
        invoice_id: int,
        new_decision: str,
        reason: str,
        actor: str = "AP_ANALYST",
        corrected_fields: Optional[Dict[str, Any]] = None
    ) -> Invoice:
        """
        Applies a human reviewer override or field correction.
        Preserves original decision, records audit log, and adjusts PO balances atomically.
        """
        if not reason or not reason.strip():
            raise APException("A mandatory justification reason must be provided for manual override.")

        invoice = self.inv_repo.get_by_id(invoice_id)
        if not invoice:
            raise APException(f"Invoice ID {invoice_id} not found.")

        old_decision = invoice.manual_override_decision or invoice.final_decision

        # Apply field corrections if supplied
        if corrected_fields:
            if "vendor_id" in corrected_fields:
                invoice.vendor_id = corrected_fields["vendor_id"]
            if "matched_po_id" in corrected_fields:
                invoice.matched_po_id = corrected_fields["matched_po_id"]
            if "invoice_number" in corrected_fields:
                invoice.invoice_number = corrected_fields["invoice_number"]
            if "total_amount" in corrected_fields:
                invoice.total_amount = float(corrected_fields["total_amount"])

        # Update PO billing balances if decision is changing to/from APPROVED
        if invoice.matched_po_id and invoice.total_amount:
            if new_decision == DecisionStatus.APPROVED.value and old_decision != DecisionStatus.APPROVED.value:
                self.po_repo.update_billed_balance_atomic(
                    po_id=invoice.matched_po_id,
                    invoice_amount=invoice.total_amount,
                    action="ADD"
                )
            elif old_decision == DecisionStatus.APPROVED.value and new_decision != DecisionStatus.APPROVED.value:
                self.po_repo.update_billed_balance_atomic(
                    po_id=invoice.matched_po_id,
                    invoice_amount=invoice.total_amount,
                    action="REVERT"
                )

        # Update invoice override fields
        invoice.manual_override_decision = new_decision
        invoice.manual_override_reason = reason.strip()
        invoice.manual_override_by = actor.strip()
        invoice.manual_override_at = datetime.now(timezone.utc)

        # Audit log entry
        self.audit_repo.log(
            action="MANUAL_OVERRIDE",
            invoice_id=invoice.id,
            details=f"Human override applied by {actor}. Reason: '{reason}'. Corrected fields: {corrected_fields}",
            previous_status=old_decision,
            new_status=new_decision,
            actor=actor
        )

        self.session.commit()
        logger.info(f"Manual override applied on Invoice {invoice_id}: {old_decision} -> {new_decision} by {actor}")
        return invoice
