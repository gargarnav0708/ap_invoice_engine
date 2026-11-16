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
    ExtractedInvoice, NormalizedInvoice, DecisionStatus, RuleStatus, RuleSeverity,
    RuleResult, LineItemMatch
)
from app.database.entities import (
    Invoice, InvoiceLineItem, ProcessingRun, ValidationResultEntity,
    Vendor, PurchaseOrder
)
from app.database.repositories import (
    InvoiceRepository, VendorRepository, PORepository, AuditRepository
)
from app.utils.file_storage import save_uploaded_file, validate_document_bytes
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
        start_time_perf = time.perf_counter()
        start_time_dt = datetime.now(timezone.utc)

        # Temporary placeholders before entity persistence
        invoice_entity: Optional[Invoice] = None
        run_entity: Optional[ProcessingRun] = None

        def emit_stage_event(event: dict) -> None:
            """Persist the visible run trace as it advances, then notify the UI."""
            if run_entity is not None:
                run_entity.current_stage = (
                    "COMPLETED"
                    if run_entity.execution_status == "SUCCESS"
                    else event.get("stage_name", run_entity.current_stage)
                )
                stage_records = [record.model_dump() for record in exec_logger.records]
                if event.get("type") == "start":
                    stage_records.append({
                        "stage_name": event.get("stage_name"),
                        "status": "RUNNING",
                        "start_time": event.get("start_time"),
                        "completion_time": None,
                        "duration_ms": 0.0,
                        "details": None,
                        "error": None,
                    })
                run_entity.stages_log = json.dumps(stage_records)
                try:
                    # Each saved run trace remains useful if the browser or app
                    # stops before the invoice reaches its final decision.
                    self.session.commit()
                except Exception:
                    self.session.rollback()
                    logger.warning("Could not persist an intermediate processing-stage update.", exc_info=True)
            if progress_callback:
                progress_callback(event)

        exec_logger = ExecutionLogger(on_event=emit_stage_event)

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

            # Stage 2: Validate uploaded document
            exec_logger.start_stage("2. Validate uploaded file")
            if file_bytes:
                validate_document_bytes(file_bytes, original_filename)
                file_path, safe_filename = save_uploaded_file(file_bytes, original_filename)
            else:
                safe_filename = file_path.name
            exec_logger.complete_stage(f"Validated document format and size. Stored at {file_path.name}")

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

            # Stage 5: Extract document text & Stage 6: Run OCR when necessary
            exec_logger.start_stage("5. Extract document text")
            ingestion_res = self.pdf_loader.load_document(file_path)
            exec_logger.complete_stage(f"Extracted {len(ingestion_res.full_text)} chars across {ingestion_res.total_pages} page(s).")

            exec_logger.start_stage("6. Run OCR when necessary")
            if ingestion_res.ocr_unavailable:
                exec_logger.complete_stage(
                    "OCR was needed but unavailable or returned no text on at least one scanned page; invoice will be routed to review."
                )
            elif ingestion_res.ocr_applied:
                exec_logger.complete_stage("OCR engine engaged for scanned/low-text content.")
            else:
                exec_logger.complete_stage("Machine-readable text sufficient. OCR bypassed.")

            # Stage 7: Extract structured invoice information
            exec_logger.start_stage("7. Extract structured invoice information")
            if ingestion_res.ocr_unavailable and not ingestion_res.full_text.strip():
                # Never ask a model to infer invoice fields from an OCR placeholder
                # or an empty document. Missing data will deterministically route
                # the invoice to a reviewer.
                extracted_data = ExtractedInvoice(raw_text="", document_type=ingestion_res.document_type)
            else:
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
            if ingestion_res.ocr_unavailable:
                validation_results.append(RuleResult(
                    rule_id="RULE_OCR_UNAVAILABLE",
                    rule_name="Scanned Page OCR Availability",
                    category="EXTRACTION",
                    result=RuleStatus.FAIL,
                    severity=RuleSeverity.SOFT,
                    expected_value="Readable text for every scanned page",
                    actual_value="OCR unavailable or returned no text",
                    explanation="At least one scanned page could not be transcribed. Review the source PDF before approving."
                ))
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
            # Keep the invoice provisional until its PO balance is reserved at
            # Stage 18. This prevents a saved APPROVED state without a matching
            # financial reservation if the process stops between stages.
            invoice_entity.processing_status = "PROCESSING"
            invoice_entity.final_decision = DecisionStatus.NEEDS_REVIEW.value

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
            late_duplicate = None
            if decision.status == DecisionStatus.APPROVED and matched_vendor:
                # Stage 17 commits the candidate invoice before the financial
                # reservation. Recheck here so a concurrent submission with the
                # same file or vendor invoice number cannot also be approved.
                late_duplicate = self.duplicate_detector.check_duplicate(
                    file_hash=file_hash,
                    invoice=normalized,
                    vendor_id=matched_vendor.id,
                    current_invoice_id=invoice_entity.id
                )
                if late_duplicate.is_duplicate and late_duplicate.severity == "CONFIRMED":
                    duplicate_rule = RuleResult(
                        rule_id="RULE_CONFIRMED_DUPLICATE",
                        rule_name="Confirmed Duplicate Invoice Check",
                        category="DUPLICATE",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value="Unique vendor invoice submission",
                        actual_value=f"Duplicate of Invoice ID {late_duplicate.matched_invoice_id}",
                        explanation=late_duplicate.explanation
                    )
                    validation_results.append(duplicate_rule)
                    decision = self.decision_service.determine_decision(validation_results)
                    invoice_entity.final_decision = decision.status.value
                    self.session.add(ValidationResultEntity(
                        invoice_id=invoice_entity.id,
                        run_id=run_entity.id,
                        rule_id=duplicate_rule.rule_id,
                        rule_name=duplicate_rule.rule_name,
                        category=duplicate_rule.category,
                        result=duplicate_rule.result.value,
                        severity=duplicate_rule.severity.value,
                        expected_value=duplicate_rule.expected_value,
                        actual_value=duplicate_rule.actual_value,
                        difference=duplicate_rule.difference,
                        explanation=duplicate_rule.explanation,
                        evidence=duplicate_rule.evidence
                    ))

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
                    exec_logger.complete_stage(f"PO reservation failed safely: {err}")
                    # The earlier balance check may have raced another invoice.
                    # Reclassify before commit and persist the failed reservation.
                    reservation_rule = RuleResult(
                        rule_id="RULE_PO_BALANCE_RESERVATION",
                        rule_name="PO Balance Reservation",
                        category="BALANCE",
                        result=RuleStatus.FAIL,
                        severity=RuleSeverity.HARD,
                        expected_value="Open PO with sufficient remaining balance at commit time",
                        actual_value=err or "Reservation failed",
                        explanation="The PO balance changed during processing or the PO is no longer open; this invoice was not approved."
                    )
                    validation_results.append(reservation_rule)
                    decision = self.decision_service.determine_decision(validation_results)
                    invoice_entity.final_decision = decision.status.value
                    self.session.add(ValidationResultEntity(
                        invoice_id=invoice_entity.id,
                        run_id=run_entity.id,
                        rule_id=reservation_rule.rule_id,
                        rule_name=reservation_rule.rule_name,
                        category=reservation_rule.category,
                        result=reservation_rule.result.value,
                        severity=reservation_rule.severity.value,
                        expected_value=reservation_rule.expected_value,
                        actual_value=reservation_rule.actual_value,
                        difference=reservation_rule.difference,
                        explanation=reservation_rule.explanation,
                        evidence=reservation_rule.evidence
                    ))
            else:
                if late_duplicate and late_duplicate.is_duplicate:
                    exec_logger.complete_stage("PO balance unchanged; duplicate submission was blocked before reservation.")
                else:
                    exec_logger.complete_stage("PO balance unchanged (Invoice not approved or no PO linked).")

            invoice_entity.processing_status = "COMPLETED"
            invoice_entity.final_decision = decision.status.value

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
            duration_total_ms = (time.perf_counter() - start_time_perf) * 1000.0
            run_entity.completion_time = datetime.now(timezone.utc)
            run_entity.current_stage = "COMPLETED"
            run_entity.execution_status = "SUCCESS"
            run_entity.final_decision = decision.status.value
            run_entity.duration_ms = round(duration_total_ms, 2)
            exec_logger.complete_stage("Audit log entry successfully created.")
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
        if not actor or not actor.strip():
            raise APException("Reviewer name or employee ID is required for the audit trail.")
        if new_decision not in {status.value for status in DecisionStatus}:
            raise APException(f"Unsupported decision: {new_decision}.")

        invoice = self.inv_repo.get_by_id(invoice_id)
        if not invoice:
            raise APException(f"Invoice ID {invoice_id} not found.")

        old_decision = invoice.manual_override_decision or invoice.final_decision
        old_po_id = invoice.matched_po_id
        old_amount = round(float(invoice.total_amount or 0.0), 2)
        old_reason = invoice.manual_override_reason
        old_actor = invoice.manual_override_by

        def snapshot() -> Dict[str, Any]:
            vendor = self.vendor_repo.get_by_id(invoice.vendor_id) if invoice.vendor_id else None
            po = self.po_repo.get_by_id(invoice.matched_po_id) if invoice.matched_po_id else None
            return {
                "decision": invoice.manual_override_decision or invoice.final_decision,
                "vendor_id": invoice.vendor_id,
                "vendor_name": vendor.legal_name if vendor else None,
                "matched_po_id": invoice.matched_po_id,
                "po_number": po.po_number if po else None,
                "invoice_number": invoice.invoice_number,
                "total_amount": round(float(invoice.total_amount), 2) if invoice.total_amount is not None else None,
            }

        before = snapshot()

        target_vendor_id = invoice.vendor_id
        target_po_id = invoice.matched_po_id
        target_invoice_number = invoice.invoice_number
        target_amount = round(float(invoice.total_amount), 2) if invoice.total_amount is not None else None

        # Validate all proposed corrections before mutating the ORM object so a
        # rejected form submission cannot be committed by the caller's session.
        if corrected_fields:
            allowed_fields = {"vendor_id", "matched_po_id", "invoice_number", "total_amount"}
            unexpected = set(corrected_fields) - allowed_fields
            if unexpected:
                raise APException(f"Unsupported correction fields: {', '.join(sorted(unexpected))}.")
            if "vendor_id" in corrected_fields:
                try:
                    target_vendor_id = int(corrected_fields["vendor_id"]) if corrected_fields["vendor_id"] is not None else None
                except (TypeError, ValueError) as exc:
                    raise APException("Corrected vendor ID must be a valid integer.") from exc
                if target_vendor_id is not None and not self.vendor_repo.get_by_id(target_vendor_id):
                    raise APException(f"Vendor ID {target_vendor_id} not found.")
            if "matched_po_id" in corrected_fields:
                try:
                    target_po_id = int(corrected_fields["matched_po_id"]) if corrected_fields["matched_po_id"] is not None else None
                except (TypeError, ValueError) as exc:
                    raise APException("Corrected purchase order ID must be a valid integer.") from exc
                if target_po_id is not None and not self.po_repo.get_by_id(target_po_id):
                    raise APException(f"Purchase Order ID {target_po_id} not found.")
            if "invoice_number" in corrected_fields:
                target_invoice_number = str(corrected_fields["invoice_number"]).strip() or None
            if "total_amount" in corrected_fields:
                try:
                    corrected_amount = round(float(corrected_fields["total_amount"]), 2)
                except (TypeError, ValueError) as exc:
                    raise APException("Corrected invoice amount must be a valid number.") from exc
                if corrected_amount < 0:
                    raise APException("Corrected invoice amount cannot be negative.")
                target_amount = corrected_amount

        if target_po_id is not None and target_vendor_id is None:
            raise APException("A linked purchase order requires a linked vendor.")
        if target_po_id is not None and target_vendor_id is not None:
            corrected_po = self.po_repo.get_by_id(target_po_id)
            if corrected_po and corrected_po.vendor_id != target_vendor_id:
                raise APException("The corrected purchase order must belong to the corrected vendor.")

        invoice.vendor_id = target_vendor_id
        invoice.matched_po_id = target_po_id
        invoice.invoice_number = target_invoice_number
        invoice.total_amount = target_amount

        new_po_id = invoice.matched_po_id
        new_amount = round(float(invoice.total_amount or 0.0), 2)

        after = snapshot()
        after["decision"] = new_decision
        changed_fields = {
            key: {"before": before[key], "after": after[key]}
            for key in before
            if before[key] != after[key]
        }
        clean_reason = reason.strip()
        clean_actor = actor.strip()
        if (
            invoice.manual_override_at is not None
            and not changed_fields
            and old_decision == new_decision
            and old_reason == clean_reason
            and old_actor == clean_actor
        ):
            # A repeated form submission with the same review payload must not
            # create duplicate audit entries or reserve the PO balance again.
            invoice.manual_override_noop = True
            return invoice

        po_or_amount_changed = old_po_id != new_po_id or old_amount != new_amount
        try:
            # Reverse the previously approved reservation before applying the
            # corrected one. Both operations share this DB transaction, so a
            # failed replacement rolls back the reversal too.
            if old_decision == DecisionStatus.APPROVED.value and (
                new_decision != DecisionStatus.APPROVED.value or po_or_amount_changed
            ) and old_po_id and old_amount > 0:
                ok, error = self.po_repo.update_billed_balance_atomic(old_po_id, old_amount, action="REVERT")
                if not ok:
                    raise APException(f"Could not release the original PO reservation: {error}")

            if new_decision == DecisionStatus.APPROVED.value and (
                old_decision != DecisionStatus.APPROVED.value or po_or_amount_changed
            ):
                if new_po_id:
                    if new_amount <= 0:
                        raise APException("An approved PO invoice requires a positive total amount.")
                    ok, error = self.po_repo.update_billed_balance_atomic(new_po_id, new_amount, action="ADD")
                    if not ok:
                        raise APException(f"Could not reserve the corrected PO balance: {error}")
        except APException:
            self.session.rollback()
            raise

        # Update invoice override fields
        invoice.manual_override_decision = new_decision
        invoice.manual_override_reason = clean_reason
        invoice.manual_override_by = clean_actor
        invoice.manual_override_at = datetime.now(timezone.utc)

        # Audit log entry
        self.audit_repo.log(
            action="MANUAL_OVERRIDE",
            invoice_id=invoice.id,
            details=json.dumps({
                "version": 1,
                "actor": clean_actor,
                "reason": clean_reason,
                "before": before,
                "after": after,
                "changes": changed_fields,
            }, sort_keys=True),
            previous_status=old_decision,
            new_status=new_decision,
            actor=clean_actor
        )

        self.session.commit()
        logger.info(f"Manual override applied on Invoice {invoice_id}: {old_decision} -> {new_decision} by {clean_actor}")
        return invoice
