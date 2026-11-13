"""
Streamlit Dashboard View: Real-time AP metrics, KPIs, and Plotly visualisations.
All metrics are dynamically calculated from the database.
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.entities import Invoice, ProcessingRun, Vendor
from app.core.schemas import DecisionStatus


def render_dashboard(session: Session):
    st.markdown("## 📊 AP Operations & Intelligence Dashboard")
    st.caption("Live metrics derived from enterprise accounts payable processing pipeline.")

    # 1. Fetch live metrics from database
    total_invoices = session.query(Invoice).count()

    approved_count = session.query(Invoice).filter(
        func.coalesce(Invoice.manual_override_decision, Invoice.final_decision) == DecisionStatus.APPROVED.value
    ).count()

    review_count = session.query(Invoice).filter(
        func.coalesce(Invoice.manual_override_decision, Invoice.final_decision) == DecisionStatus.NEEDS_REVIEW.value
    ).count()

    blocked_count = session.query(Invoice).filter(
        func.coalesce(Invoice.manual_override_decision, Invoice.final_decision) == DecisionStatus.BLOCKED.value
    ).count()

    total_spend = session.query(func.sum(Invoice.total_amount)).filter(
        func.coalesce(Invoice.manual_override_decision, Invoice.final_decision) == DecisionStatus.APPROVED.value
    ).scalar() or 0.0

    avg_duration = session.query(func.avg(ProcessingRun.duration_ms)).scalar() or 0.0

    approval_rate = (approved_count / total_invoices * 100) if total_invoices > 0 else 0.0
    exception_rate = (review_count / total_invoices * 100) if total_invoices > 0 else 0.0

    # 2. Metric KPI Cards
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.metric("Total Invoices", f"{total_invoices:,}")
    with col2:
        st.metric("Approved", f"{approved_count}", delta=f"{approval_rate:.1f}% rate")
    with col3:
        st.metric("Needs Review", f"{review_count}", delta=f"{exception_rate:.1f}% rate", delta_color="inverse")
    with col4:
        st.metric("Blocked", f"{blocked_count}", delta_color="inverse")
    with col5:
        st.metric("Total Approved Spend", f"${total_spend:,.2f}")
    with col6:
        st.metric("Avg Speed", f"{avg_duration:.0f} ms")

    st.markdown("---")

    # 3. Visualizations
    col_chart1, col_chart2 = st.columns([1, 1])

    with col_chart1:
        st.subheader("Decision Breakdown")
        if total_invoices > 0:
            status_df = pd.DataFrame([
                {"Status": "Approved", "Count": approved_count, "Color": "#38A169"},
                {"Status": "Needs Review", "Count": review_count, "Color": "#DD6B20"},
                {"Status": "Blocked", "Count": blocked_count, "Color": "#E53E3E"},
            ])
            fig_donut = px.pie(
                status_df,
                names="Status",
                values="Count",
                hole=0.55,
                color="Status",
                color_discrete_map={
                    "Approved": "#38A169",
                    "Needs Review": "#DD6B20",
                    "Blocked": "#E53E3E"
                }
            )
            fig_donut.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280)
            st.plotly_chart(fig_donut, use_container_width=True)
        else:
            st.info("No invoice records in database yet. Process an invoice to view metrics.")

    with col_chart2:
        st.subheader("Approved Spend by Vendor")
        vendor_spend_query = session.query(
            Vendor.legal_name,
            func.sum(Invoice.total_amount).label("total_spend")
        ).join(Invoice, Invoice.vendor_id == Vendor.id).filter(
            func.coalesce(Invoice.manual_override_decision, Invoice.final_decision) == DecisionStatus.APPROVED.value
        ).group_by(Vendor.legal_name).all()

        if vendor_spend_query:
            spend_df = pd.DataFrame(vendor_spend_query, columns=["Vendor", "Spend"])
            fig_bar = px.bar(
                spend_df,
                x="Vendor",
                y="Spend",
                text_auto=".2s",
                color_discrete_sequence=["#2B6CB0"]
            )
            fig_bar.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280)
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("No approved invoice spend recorded yet.")

    st.markdown("---")

    # 4. Recent Processing Activity Table
    st.subheader("Recent Processing Runs")
    recent_invoices = session.query(Invoice).order_by(Invoice.created_at.desc()).limit(10).all()

    if recent_invoices:
        rows = []
        for inv in recent_invoices:
            effective_dec = inv.manual_override_decision or inv.final_decision
            vendor_name = inv.vendor.legal_name if inv.vendor else "Unknown / Unmatched"
            rows.append({
                "ID": inv.id,
                "Filename": inv.original_filename,
                "Vendor": vendor_name,
                "Invoice #": inv.invoice_number or "N/A",
                "Amount": f"${inv.total_amount:.2f}" if inv.total_amount is not None else "N/A",
                "Confidence": f"{inv.extraction_confidence*100:.0f}%",
                "Status": effective_dec,
                "Processed At": inv.created_at.strftime("%Y-%m-%d %H:%M:%S")
            })

        df_recent = pd.DataFrame(rows)
        st.dataframe(
            df_recent,
            use_container_width=True,
            column_config={
                "Status": st.column_config.TextColumn(
                    "Decision",
                    help="Final or manually overridden status"
                )
            }
        )
    else:
        st.caption("No invoices processed yet. Navigate to 'Upload Invoice' to begin.")
