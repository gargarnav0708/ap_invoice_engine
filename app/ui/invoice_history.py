"""
Streamlit Invoice History View: searchable, filterable, database-paginated
audit registry of all invoices.

Search, vendor and status filters are applied in SQL, the filtered rows are
counted, and only the selected page is fetched (LIMIT/OFFSET) in a stable
``created_at DESC, id DESC`` order.
"""
import streamlit as st
from sqlalchemy.orm import Session

from app.config import ui as ui_config
from app.database.queries import InvoiceFilters, paginate_invoices, vendor_options
from app.processing.currency import convert_to_inr, format_inr
from app.processing.exporter import (
    to_audit_json,
    to_generic_csv,
    to_quickbooks_bills_csv,
    to_xero_bills_csv,
)
from app.ui.components import render_pager, render_table, store_page, sync_pagination
from app.ui.presentation import FILTERABLE_STATUSES, invoice_display_status, status_meta

PAGER_PREFIX = "history"


def render_invoice_history(session: Session):
    st.markdown("## 📁 Invoice Repository & Audit History")
    st.caption("Search, filter, and inspect all processed AP invoices across procurement cycles.")

    vendors = vendor_options(session)
    vendor_labels = dict(vendors)

    # Filter controls: 2 + 2 layout stacks cleanly on tablets and phones.
    row1 = st.columns([2, 1])
    with row1[0]:
        search_query = st.text_input(
            "Search invoice #, PO reference, or filename",
            placeholder="e.g. INV-2026, PO-2026…",
            key="history_search",
        )
    with row1[1]:
        selected_status = st.selectbox(
            "Status",
            options=[None, *FILTERABLE_STATUSES],
            format_func=lambda s: "All statuses" if s is None else f"{status_meta(s).symbol} {status_meta(s).label}",
            key="history_status",
        )
    row2 = st.columns([2, 1])
    with row2[0]:
        selected_vendor = st.selectbox(
            "Vendor",
            options=[None, *vendor_labels.keys()],
            format_func=lambda v: "All vendors" if v is None else vendor_labels.get(v, f"Vendor {v}"),
            key="history_vendor",
        )
    with row2[1]:
        page_size = st.selectbox(
            "Rows per page",
            options=list(ui_config.PAGE_SIZE_OPTIONS),
            index=list(ui_config.PAGE_SIZE_OPTIONS).index(ui_config.DEFAULT_PAGE_SIZE),
            key="history_page_size",
        )

    filters = InvoiceFilters(search=search_query or "", vendor_id=selected_vendor, status=selected_status)
    requested = sync_pagination(PAGER_PREFIX, filters.signature() + (page_size,))
    invoices, info = paginate_invoices(session, filters, requested, page_size)
    store_page(PAGER_PREFIX, info)

    if not invoices:
        st.info("No invoices found matching the selected search criteria.")
        render_pager(PAGER_PREFIX, info)
        return

    table_rows = []
    for inv in invoices:
        inr_eq = convert_to_inr(inv.total_amount, inv.currency or "USD") if inv.total_amount is not None else None
        table_rows.append({
            "ID": inv.id,
            "Decision": invoice_display_status(inv.final_decision, inv.processing_status,
                                               inv.manual_override_decision),
            "Invoice #": inv.invoice_number or "N/A",
            "Vendor": inv.vendor.legal_name if inv.vendor else "Unmatched",
            "PO Ref": inv.po_reference or "N/A",
            "Amount": (f"{inv.currency + ' ' if inv.currency else ''}{inv.total_amount:,.2f}"
                       if inv.total_amount is not None else "N/A"),
            "Amount (INR ₹)": format_inr(inr_eq),
            "Currency": inv.currency or "Unconfirmed",
            "Paid Date": inv.paid_date or "—",
            "Confidence": f"{(inv.extraction_confidence or 0) * 100:.0f}%",
            "Processed (UTC)": inv.created_at.strftime("%Y-%m-%d %H:%M"),
        })

    render_table(
        table_rows,
        ["ID", "Decision", "Invoice #", "Vendor", "PO Ref", "Amount", "Amount (INR ₹)", "Currency", "Paid Date",
         "Confidence", "Processed (UTC)"],
        caption=f"Invoices, page {info.page} of {info.total_pages}",
        column_kinds={"Decision": "status"},
        numeric_columns=["ID", "Amount", "Amount (INR ₹)", "Confidence"],
    )
    render_pager(PAGER_PREFIX, info)

    # Quick navigation to the detail workbench (current page only).
    st.markdown("#### Open an invoice")
    col_pick, col_btn = st.columns([3, 1], vertical_alignment="bottom")
    with col_pick:
        selected_inv_id = st.selectbox(
            "Invoice on this page",
            options=[inv.id for inv in invoices],
            format_func=lambda x: next(
                (f"#{i.id} · {i.invoice_number or 'No invoice #'} · {i.original_filename}" for i in invoices if i.id == x),
                f"Invoice #{x}",
            ),
            key="history_open_select",
        )
    with col_btn:
        if st.button("🔎 Open details", type="primary", width="stretch"):
            st.session_state["selected_invoice_id"] = selected_inv_id
            st.session_state["current_page"] = "Invoice Details"
            st.rerun()

    st.markdown("#### Export current page for accounting / audit")
    ex1, ex2, ex3, ex4 = st.columns(4)
    with ex1:
        st.download_button(
            "⬇️ Audit CSV",
            data=to_generic_csv(invoices),
            file_name=f"ap_invoices_page_{info.page}.csv",
            mime="text/csv",
            width="stretch",
        )
    with ex2:
        st.download_button(
            "⬇️ Xero Bills CSV",
            data=to_xero_bills_csv(invoices),
            file_name=f"xero_bills_page_{info.page}.csv",
            mime="text/csv",
            width="stretch",
        )
    with ex3:
        st.download_button(
            "⬇️ QuickBooks CSV",
            data=to_quickbooks_bills_csv(invoices),
            file_name=f"quickbooks_bills_page_{info.page}.csv",
            mime="text/csv",
            width="stretch",
        )
    with ex4:
        st.download_button(
            "⬇️ Audit JSON",
            data=to_audit_json(invoices),
            file_name=f"ap_invoices_page_{info.page}.json",
            mime="application/json",
            width="stretch",
        )
