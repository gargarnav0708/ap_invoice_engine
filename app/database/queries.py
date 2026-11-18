"""
Read-side query helpers for the UI.

Filtering, counting and pagination happen in SQL so the UI never loads an
unbounded invoice or run list. Ordering is deterministic (``created_at DESC,
id DESC``) so rows with identical timestamps never jump between pages.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.database.entities import Invoice, ProcessingRun, Vendor


def display_status_expression() -> ColumnElement[str]:
    """SQL twin of ``app.ui.presentation.invoice_display_status``."""
    return case(
        (Invoice.manual_override_decision.is_not(None), Invoice.manual_override_decision),
        (Invoice.processing_status == "FAILED", "FAILED"),
        (Invoice.processing_status.in_(("PENDING", "PROCESSING")), "IN_PROGRESS"),
        else_=Invoice.final_decision,
    )


@dataclass(frozen=True)
class InvoiceFilters:
    search: str = ""
    vendor_id: Optional[int] = None
    status: Optional[str] = None

    def signature(self) -> tuple:
        """Stable value used by the UI to detect filter changes and reset paging."""
        return (self.search.strip().lower(), self.vendor_id, self.status)


@dataclass(frozen=True)
class PageInfo:
    page: int          # 1-based, always within [1, total_pages]
    page_size: int
    total: int
    total_pages: int   # at least 1 so "Page 1 of 1" is shown for empty results

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def first_row(self) -> int:
        return 0 if self.total == 0 else self.offset + 1

    @property
    def last_row(self) -> int:
        return min(self.offset + self.page_size, self.total)


def resolve_page(requested_page: int, page_size: int, total: int) -> PageInfo:
    """Clamp a requested page into range for the given result count."""
    size = max(1, int(page_size))
    total = max(0, int(total))
    total_pages = max(1, math.ceil(total / size))
    page = min(max(1, int(requested_page)), total_pages)
    return PageInfo(page=page, page_size=size, total=total, total_pages=total_pages)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered_invoice_ids(filters: InvoiceFilters):
    stmt = select(Invoice.id)
    term = filters.search.strip()
    if term:
        pattern = f"%{_escape_like(term)}%"
        stmt = stmt.where(or_(
            Invoice.invoice_number.ilike(pattern, escape="\\"),
            Invoice.po_reference.ilike(pattern, escape="\\"),
            Invoice.original_filename.ilike(pattern, escape="\\"),
        ))
    if filters.vendor_id is not None:
        stmt = stmt.where(Invoice.vendor_id == filters.vendor_id)
    if filters.status:
        stmt = stmt.where(display_status_expression() == filters.status)
    return stmt


def count_invoices(session: Session, filters: InvoiceFilters) -> int:
    sub = _filtered_invoice_ids(filters).subquery()
    return int(session.execute(select(func.count()).select_from(sub)).scalar_one())


def fetch_invoice_page(session: Session, filters: InvoiceFilters, page: PageInfo) -> list[Invoice]:
    """Fetch only the rows of the requested page (LIMIT/OFFSET in SQL)."""
    stmt = (
        select(Invoice)
        .where(Invoice.id.in_(_filtered_invoice_ids(filters)))
        .options(
            joinedload(Invoice.vendor),
            joinedload(Invoice.matched_po),
            selectinload(Invoice.line_items),
        )
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .limit(page.page_size)
        .offset(page.offset)
    )
    return list(session.execute(stmt).scalars().unique())


def paginate_invoices(session: Session, filters: InvoiceFilters,
                      requested_page: int, page_size: int) -> tuple[list[Invoice], PageInfo]:
    total = count_invoices(session, filters)
    info = resolve_page(requested_page, page_size, total)
    return (fetch_invoice_page(session, filters, info) if total else []), info


def vendor_options(session: Session) -> list[tuple[int, str]]:
    """Vendors for filter widgets (reference data, ordered by name)."""
    rows = session.execute(select(Vendor.id, Vendor.legal_name).order_by(Vendor.legal_name, Vendor.id))
    return [(int(r[0]), str(r[1])) for r in rows]


def get_invoice(session: Session, invoice_id: Optional[int]) -> Optional[Invoice]:
    if invoice_id is None:
        return None
    return session.get(Invoice, invoice_id)


@dataclass(frozen=True)
class RunSummary:
    """Lightweight run row for selectors; excludes the large ``stages_log``."""
    id: int
    invoice_id: int
    execution_status: str
    final_decision: Optional[str]
    start_time: object


def list_recent_runs(session: Session, limit: int, include_run_id: Optional[int] = None) -> list[RunSummary]:
    """Most recent runs (bounded), plus ``include_run_id`` if it fell outside the window."""
    cols = (ProcessingRun.id, ProcessingRun.invoice_id, ProcessingRun.execution_status,
            ProcessingRun.final_decision, ProcessingRun.start_time)
    rows = session.execute(
        select(*cols).order_by(ProcessingRun.start_time.desc(), ProcessingRun.id.desc()).limit(max(1, limit))
    ).all()
    summaries = [RunSummary(*row) for row in rows]
    if include_run_id is not None and all(s.id != include_run_id for s in summaries):
        extra = session.execute(select(*cols).where(ProcessingRun.id == include_run_id)).first()
        if extra is not None:
            summaries.insert(0, RunSummary(*extra))
    return summaries


def get_run(session: Session, run_id: Optional[int]) -> Optional[ProcessingRun]:
    if run_id is None:
        return None
    return session.get(ProcessingRun, run_id)


def latest_run_for_invoice(session: Session, invoice_id: int) -> Optional[ProcessingRun]:
    return session.execute(
        select(ProcessingRun)
        .where(ProcessingRun.invoice_id == invoice_id)
        .order_by(ProcessingRun.start_time.desc(), ProcessingRun.id.desc())
        .limit(1)
    ).scalars().first()


def status_counts(session: Session) -> dict[str, int]:
    """Invoice counts grouped by display status (one aggregate query)."""
    expr = display_status_expression()
    rows = session.execute(select(expr, func.count(Invoice.id)).group_by(expr)).all()
    return {str(status): int(count) for status, count in rows if status is not None}
