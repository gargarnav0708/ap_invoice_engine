"""
Thin Streamlit wrappers around the pure presentation helpers.
"""
from __future__ import annotations

from html import escape
from typing import Any, Iterable, Mapping, Optional, Sequence

import streamlit as st

from app.database.queries import PageInfo
from app.ui.presentation import Kpi, badge_html, kpi_grid_html, status_meta, table_html


def html(markup: str) -> None:
    st.html(markup)


def render_kpis(items: Sequence[Kpi], label: str = "Key metrics") -> None:
    st.html(kpi_grid_html(items, label))


def render_table(rows: Iterable[Mapping[str, Any]], columns: Sequence[str], *, caption: str,
                 column_kinds: Optional[Mapping[str, str]] = None, wrap_columns: Iterable[str] = (),
                 numeric_columns: Iterable[str] = (), empty_message: str = "No records.") -> None:
    st.html(table_html(rows, columns, caption=caption, column_kinds=column_kinds,
                       wrap_columns=wrap_columns, numeric_columns=numeric_columns,
                       empty_message=empty_message))


def render_status_banner(status: Optional[str], title: str = "", body: str = "", size: str = "lg") -> None:
    """Decision/status card: badge (symbol + text) plus optional explanation."""
    meta = status_meta(status)
    title_html = f"<span>{escape(title)}</span>" if title else ""
    body_html = f'<div class="ap-decision__body">{escape(body)}</div>' if body else ""
    st.html(
        f'<div class="ap-decision ap-decision--{meta.tone}" role="status" aria-label="Status: {escape(meta.label)}">'
        f'<div class="ap-decision__title">{badge_html(status, size=size)}{title_html}</div>{body_html}</div>'
    )


def sync_pagination(prefix: str, signature: tuple) -> int:
    """Return the requested page, resetting to 1 when filters/page size change."""
    sig_key, page_key = f"{prefix}_sig", f"{prefix}_page"
    if st.session_state.get(sig_key) != signature:
        st.session_state[sig_key] = signature
        st.session_state[page_key] = 1
    return int(st.session_state.get(page_key, 1))


def store_page(prefix: str, info: PageInfo) -> None:
    st.session_state[f"{prefix}_page"] = info.page


def _set_page(prefix: str, page: int) -> None:
    st.session_state[f"{prefix}_page"] = page


def render_pager(prefix: str, info: PageInfo, noun: str = "invoices") -> None:
    """Previous/next controls with page and row-range information."""
    col_prev, col_info, col_next = st.columns([1, 2, 1], vertical_alignment="center")
    with col_prev:
        st.button("‹ Previous", key=f"{prefix}_prev", disabled=not info.has_prev, width="stretch",
                  on_click=_set_page, args=(prefix, info.page - 1),
                  help="Go to the previous page")
    with col_info:
        if info.total:
            text = (f"Page {info.page} of {info.total_pages} · showing {info.first_row}–{info.last_row} "
                    f"of {info.total} {noun}")
        else:
            text = f"Page 1 of 1 · no matching {noun}"
        st.html(f'<div class="ap-pager" role="status" aria-live="polite" style="text-align:center">{escape(text)}</div>')
    with col_next:
        st.button("Next ›", key=f"{prefix}_next", disabled=not info.has_next, width="stretch",
                  on_click=_set_page, args=(prefix, info.page + 1),
                  help="Go to the next page")
