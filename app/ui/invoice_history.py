"""
Streamlit Invoice History View: Searchable, filterable audit registry of all invoices.
"""
import streamlit as st
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.entities import Invoice, Vendor
from app.core.schemas import DecisionStatus


def render_invoice_history(session: Session):
    st.markdown("## 📁 Invoice Repository & Audit History")
    st.caption("Search, filter, and inspect all processed AP invoices across procurement cycles.")

    # Fetch filter options
    vendors = session.query(Vendor).all()
    vendor_names = ["All Vendors"] + [v.legal_name for v in vendors]
    status_options = ["All Statuses", "APPROVED", "NEEDS_REVIEW", "BLOCKED"]

    # Filter controls
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        search_query = st.text_input("🔍 Search Invoice #, PO, or Filename", placeholder="e.g. INV-2026, PO-2026...")
    with col_f2:
        selected_vendor = st.selectbox("Filter by Vendor", options=vendor_names)
    with col_f3:
        selected_status = st.selectbox("Filter by Status", options=status_options)

    # Build query
    query = session.query(Invoice).outerjoin(Vendor, Invoice.vendor_id == Vendor.id)

    if search_query.strip():
        term = f"%{search_query.strip()}%"
        query = query.filter(
            (Invoice.invoice_number.ilike(term)) |
            (Invoice.po_reference.ilike(term)) |
            (Invoice.original_filename.ilike(term))
        )

    if selected_vendor != "All Vendors":
        query = query.filter(Vendor.legal_name == selected_vendor)

    if selected_status != "All Statuses":
        query = query.filter(
            func.coalesce(Invoice.manual_override_decision, Invoice.final_decision) == selected_status
        )

    invoices = query.order_by(Invoice.created_at.desc()).all()

    st.write(f"Showing **{len(invoices)}** matching invoice(s)")

    if invoices:
        table_rows = []
        for inv in invoices:
            effective_status = inv.manual_override_decision or inv.final_decision
            v_name = inv.vendor.legal_name if inv.vendor else "Unmatched"
            table_rows.append({
                "Select": False,
                "ID": inv.id,
                "Invoice #": inv.invoice_number or "N/A",
                "Vendor": v_name,
                "PO Ref": inv.po_reference or "N/A",
                "Amount": f"${inv.total_amount:.2f}" if inv.total_amount is not None else "N/A",
                "Currency": inv.currency or "USD",
                "Decision": effective_status,
                "Confidence": f"{inv.extraction_confidence*100:.0f}%",
                "Processed Date": inv.created_at.strftime("%Y-%m-%d %H:%M")
            })

        df = pd.DataFrame(table_rows)
        st.dataframe(
            df.drop(columns=["Select"]),
            use_container_width=True
        )

        # Quick navigation
        st.markdown("#### Jump to Invoice Details")
        selected_inv_id = st.selectbox("Select Invoice ID to inspect:", options=[inv.id for inv in invoices], format_func=lambda x: f"Invoice #{x}")
        if st.button("🔎 Open Detailed Record", type="primary"):
            st.session_state["selected_invoice_id"] = selected_inv_id
            st.session_state["current_page"] = "Invoice Details"
            st.rerun()

    else:
        st.info("No invoices found matching the selected search criteria.")
