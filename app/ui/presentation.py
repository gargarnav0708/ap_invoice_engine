"""
Pure presentation helpers shared by every UI page.

Nothing here imports Streamlit or touches the database, so the status mapping,
badges, KPI cards and tables can be unit-tested and rendered identically on
the dashboard, history, upload results, Live Processing View and Invoice
Details. Every badge carries a text label and a symbol, so status is never
conveyed by colour alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Iterable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class StatusMeta:
    key: str
    label: str
    symbol: str
    tone: str          # CSS modifier: success | warning | danger | info | neutral
    description: str


STATUS_META: dict[str, StatusMeta] = {
    "APPROVED": StatusMeta("APPROVED", "Approved", "✓", "success",
                           "All deterministic checks passed."),
    "NEEDS_REVIEW": StatusMeta("NEEDS_REVIEW", "Needs review", "!", "warning",
                               "A human reviewer must confirm this invoice."),
    "BLOCKED": StatusMeta("BLOCKED", "Blocked", "✕", "danger",
                          "A hard control failed; the invoice must not be paid."),
    "FAILED": StatusMeta("FAILED", "Failed · needs review", "✕", "danger",
                         "Processing failed before a decision was reached; route to a reviewer."),
    "IN_PROGRESS": StatusMeta("IN_PROGRESS", "In progress", "…", "info",
                              "The pipeline is still running."),
    "STALLED": StatusMeta("STALLED", "Stalled · needs review", "!", "warning",
                          "The run stopped reporting progress; inspect it and re-process if needed."),
    "UNKNOWN": StatusMeta("UNKNOWN", "Unknown", "?", "neutral", "No status recorded."),
}

# Statuses the history filter offers (all derived in SQL, see app/database/queries.py)
FILTERABLE_STATUSES: tuple[str, ...] = ("APPROVED", "NEEDS_REVIEW", "BLOCKED", "FAILED", "IN_PROGRESS")

STAGE_STATUS_META: dict[str, tuple[str, str, str]] = {
    # status -> (symbol, label, tone)
    "COMPLETED": ("✓", "Completed", "success"),
    "RUNNING": ("…", "Running", "info"),
    "FAILED": ("✕", "Failed", "danger"),
    "SKIPPED": ("–", "Skipped", "neutral"),
    "PENDING": ("○", "Pending", "neutral"),
    "NOT_RUN": ("○", "Not run", "neutral"),
}


def status_meta(status: Optional[str]) -> StatusMeta:
    return STATUS_META.get((status or "").upper(), STATUS_META["UNKNOWN"])


def invoice_display_status(
    final_decision: Optional[str],
    processing_status: Optional[str],
    manual_override_decision: Optional[str] = None,
) -> str:
    """Single source of truth for how an invoice's status is shown.

    Mirrors ``display_status_expression`` in ``app/database/queries.py`` so the
    SQL filter and the rendered badge always agree. A reviewer override wins;
    a failed run is shown as FAILED (never as an in-progress decision).
    """
    if manual_override_decision:
        return manual_override_decision
    proc = (processing_status or "").upper()
    if proc == "FAILED":
        return "FAILED"
    if proc in {"PENDING", "PROCESSING"}:
        return "IN_PROGRESS"
    return final_decision or "UNKNOWN"


def run_display_status(execution_status: Optional[str], final_decision: Optional[str],
                       is_stale: bool = False) -> str:
    """How a processing run's outcome is shown."""
    status = (execution_status or "").upper()
    if status == "FAILED":
        return "FAILED"
    if status == "RUNNING":
        return "STALLED" if is_stale else "IN_PROGRESS"
    return final_decision or "UNKNOWN"


def badge_html(status: Optional[str], size: str = "md") -> str:
    meta = status_meta(status)
    return (
        f'<span class="ap-badge ap-badge--{meta.tone} ap-badge--{escape(size)}" '
        f'title="{escape(meta.description)}">'
        f'<span class="ap-badge__sym" aria-hidden="true">{escape(meta.symbol)}</span>'
        f'<span>{escape(meta.label)}</span></span>'
    )


def stage_badge_html(status: Optional[str]) -> str:
    symbol, label, tone = STAGE_STATUS_META.get((status or "").upper(), ("?", status or "Unknown", "neutral"))
    return (
        f'<span class="ap-badge ap-badge--{tone} ap-badge--sm">'
        f'<span class="ap-badge__sym" aria-hidden="true">{escape(symbol)}</span>'
        f'<span>{escape(label)}</span></span>'
    )


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    note: str = ""
    tone: str = "neutral"


def kpi_grid_html(items: Sequence[Kpi], label: str = "Key metrics") -> str:
    """Responsive KPI cards: an auto-fit CSS grid that shows six across on wide
    screens, three on tablets and two/one on phones without forcing columns."""
    cards = []
    for item in items:
        note = f'<div class="ap-kpi__note">{escape(item.note)}</div>' if item.note else ""
        cards.append(
            f'<div class="ap-kpi ap-kpi--{escape(item.tone)}" role="listitem">'
            f'<div class="ap-kpi__label">{escape(item.label)}</div>'
            f'<div class="ap-kpi__value">{escape(item.value)}</div>{note}</div>'
        )
    return f'<div class="ap-kpi-grid" role="list" aria-label="{escape(label)}">{"".join(cards)}</div>'


def _cell_html(value: Any, kind: str) -> str:
    if kind == "status":
        return badge_html(value, size="sm")
    if kind == "stage_status":
        return stage_badge_html(value)
    if kind == "html":
        return str(value)  # caller is responsible for escaping
    if value is None or value == "":
        return '<span class="ap-muted">—</span>'
    return escape(str(value))


def table_html(
    rows: Iterable[Mapping[str, Any]],
    columns: Sequence[str],
    *,
    caption: str,
    column_kinds: Optional[Mapping[str, str]] = None,
    wrap_columns: Iterable[str] = (),
    numeric_columns: Iterable[str] = (),
    empty_message: str = "No records.",
) -> str:
    """Accessible, theme-aware HTML table inside a keyboard-scrollable region.

    Used instead of canvas-based ``st.dataframe`` so the table follows the
    in-app light/dark theme and wraps or scrolls horizontally on narrow
    screens. All values are HTML-escaped unless the column kind is ``html``.
    """
    kinds = dict(column_kinds or {})
    wrap = set(wrap_columns)
    numeric = set(numeric_columns)
    row_list = list(rows)
    if not row_list:
        return f'<p class="ap-empty" role="status">{escape(empty_message)}</p>'

    def cls(col: str) -> str:
        parts = []
        if col in wrap:
            parts.append("ap-wrap")
        if col in numeric:
            parts.append("ap-num")
        return f' class="{" ".join(parts)}"' if parts else ""

    head = "".join(f'<th scope="col"{cls(c)}>{escape(c)}</th>' for c in columns)
    body = "".join(
        "<tr>" + "".join(
            f"<td{cls(c)}>{_cell_html(row.get(c), kinds.get(c, 'text'))}</td>" for c in columns
        ) + "</tr>"
        for row in row_list
    )
    return (
        f'<div class="ap-table-wrap" role="region" aria-label="{escape(caption)}" tabindex="0">'
        f'<table class="ap-table"><caption class="ap-sr-only">{escape(caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def format_money(amount: Optional[float], currency: Optional[str]) -> str:
    if amount is None:
        return "—"
    prefix = f"{currency} " if currency else ""
    suffix = "" if currency else " (currency unconfirmed)"
    return f"{prefix}{amount:,.2f}{suffix}"
