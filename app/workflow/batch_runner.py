"""
Batch invoice processing runner with per-document error isolation.

Processes a list of ``(filename, file_bytes)`` documents sequentially through
``WorkflowOrchestrator`` so that a corrupt document, OCR error, or duplicate
never aborts the remaining documents in the batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from app.ui.presentation import invoice_display_status
from app.workflow.orchestrator import WorkflowOrchestrator


@dataclass(frozen=True)
class BatchItemResult:
    filename: str
    status: str               # APPROVED | NEEDS_REVIEW | BLOCKED | FAILED
    invoice_id: Optional[int] = None
    run_id: Optional[int] = None
    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    confidence: Optional[float] = None
    duration_ms: Optional[float] = None
    summary: str = ""
    error: Optional[str] = None


@dataclass
class BatchRunSummary:
    results: list[BatchItemResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def approved(self) -> int:
        return sum(1 for r in self.results if r.status == "APPROVED")

    @property
    def needs_review(self) -> int:
        return sum(1 for r in self.results if r.status == "NEEDS_REVIEW")

    @property
    def blocked(self) -> int:
        return sum(1 for r in self.results if r.status == "BLOCKED")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "FAILED")


def run_invoice_batch(
    orchestrator: WorkflowOrchestrator,
    documents: Sequence[tuple[str, bytes]],
    on_item_complete: Optional[Callable[[int, int, BatchItemResult], None]] = None,
) -> BatchRunSummary:
    """Process each ``(filename, file_bytes)`` pair with per-document error isolation."""
    summary = BatchRunSummary()
    total = len(documents)
    for idx, (filename, file_bytes) in enumerate(documents, start=1):
        try:
            inv, run = orchestrator.process_invoice(
                file_bytes=file_bytes,
                original_filename=filename,
            )
            decision_obj = getattr(run, "decision_result", None)
            disp_status = invoice_display_status(
                inv.final_decision, inv.processing_status, inv.manual_override_decision
            )
            item = BatchItemResult(
                filename=filename,
                status=disp_status,
                invoice_id=inv.id,
                run_id=run.id,
                invoice_number=inv.invoice_number,
                vendor_name=inv.vendor.legal_name if inv.vendor else None,
                total_amount=inv.total_amount,
                currency=inv.currency,
                confidence=inv.extraction_confidence,
                duration_ms=run.duration_ms,
                summary=decision_obj.summary if decision_obj is not None else disp_status,
            )
        except Exception as exc:
            item = BatchItemResult(
                filename=filename,
                status="FAILED",
                summary="Pipeline error — routed to human review",
                error=str(exc),
            )
        summary.results.append(item)
        if on_item_complete is not None:
            try:
                on_item_complete(idx, total, item)
            except Exception:
                pass
    return summary
