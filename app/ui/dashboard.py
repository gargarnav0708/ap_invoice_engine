"""
Streamlit Dashboard View: Real-time AP metrics, KPIs, and Plotly visualisations.
All metrics are dynamically calculated from the database, with spend reported in
Indian Rupees (INR / ₹) via deterministic FX conversion.
"""
import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.schemas import DecisionStatus
from app.database.entities import Invoice, ProcessingRun, Vendor
from app.database.queries import display_status_expression, status_counts
from app.processing.currency import (
    convert_to_inr,
    format_inr,
    get_usd_to_inr_rate,
)
from app.ui.components import render_kpis, render_table
from app.ui.presentation import Kpi, invoice_display_status, status_meta
from app.ui.theme import chart_color, current_theme, style_figure


def render_dashboard(session: Session):
    theme = current_theme()
    usd_rate = get_usd_to_inr_rate()
    st.markdown("## 📊 AP Operations & Intelligence Dashboard")
    st.caption(
        f"Live metrics derived from enterprise accounts payable processing pipeline "
        f"(reporting currency: **INR ₹** · 1 USD = ₹{usd_rate:,.2f})."
    )

    # 1. Live metrics (single grouped query for the status breakdown)
    counts = status_counts(session)
    total_invoices = sum(counts.values())
    approved_count = counts.get(DecisionStatus.APPROVED.value, 0)
    review_count = counts.get(DecisionStatus.NEEDS_REVIEW.value, 0)
    blocked_count = counts.get(DecisionStatus.BLOCKED.value, 0)
    failed_count = counts.get("FAILED", 0)

    approved_expr = display_status_expression() == DecisionStatus.APPROVED.value
    approved_rows = session.execute(
        select(Invoice.total_amount, Invoice.currency, Vendor.legal_name)
        .outerjoin(Vendor, Invoice.vendor_id == Vendor.id)
        .where(approved_expr)
    ).all()

    total_spend_inr = 0.0
    vendor_spend_inr: dict[str, float] = {}
    for amt, curr, v_name in approved_rows:
        if amt is None:
            continue
        inr_val = convert_to_inr(float(amt), curr or "USD") or 0.0
        total_spend_inr += inr_val
        if v_name:
            vendor_spend_inr[str(v_name)] = vendor_spend_inr.get(str(v_name), 0.0) + inr_val

    avg_duration = session.execute(select(func.avg(ProcessingRun.duration_ms))).scalar() or 0.0

    approval_rate = (approved_count / total_invoices * 100) if total_invoices else 0.0
    attention = review_count + failed_count
    exception_rate = (attention / total_invoices * 100) if total_invoices else 0.0

    # 2. Responsive KPI cards (auto-fit grid: 6 → 3 → 2 → 1 across as width shrinks)
    render_kpis(
        [
            Kpi("Total invoices", f"{total_invoices:,}", tone="info"),
            Kpi("Approved", f"{approved_count:,}", f"{approval_rate:.1f}% approval rate", "success"),
            Kpi(
                "Needs review",
                f"{attention:,}",
                f"{exception_rate:.1f}% exception rate"
                + (f" · incl. {failed_count} failed" if failed_count else ""),
                "warning",
            ),
            Kpi("Blocked", f"{blocked_count:,}", "Hard control failures", "danger"),
            Kpi(
                "Approved spend (INR)",
                format_inr(total_spend_inr),
                f"FX converted @ 1 USD = ₹{usd_rate:,.2f}",
            ),
            Kpi("Avg processing time", f"{avg_duration:,.0f} ms", "Mean pipeline duration"),
        ],
        label="AP processing key metrics",
    )

    st.markdown("---")

    # 3. Visualisations (columns stack automatically on narrow screens)
    col_chart1, col_chart2 = st.columns([1, 1])

    with col_chart1:
        st.subheader("Decision Breakdown")
        if total_invoices > 0:
            keys = [k for k in ("APPROVED", "NEEDS_REVIEW", "BLOCKED", "FAILED", "IN_PROGRESS") if counts.get(k)]
            status_df = pd.DataFrame(
                [
                    {"Status": f"{status_meta(k).symbol} {status_meta(k).label}", "Count": counts[k], "key": k}
                    for k in keys
                ]
            )
            fig_donut = px.pie(
                status_df,
                names="Status",
                values="Count",
                hole=0.55,
                color="Status",
                color_discrete_map={
                    row["Status"]: chart_color(row["key"], theme) for _, row in status_df.iterrows()
                },
            )
            fig_donut.update_traces(textinfo="value+percent", textfont_size=13)
            style_figure(fig_donut, theme, height=300)
            st.plotly_chart(
                fig_donut,
                width="stretch",
                theme=None,
                config={"displayModeBar": False, "responsive": True},
            )
        else:
            st.info("No invoice records in database yet. Process an invoice to view metrics.")

    with col_chart2:
        st.subheader("Approved Spend by Vendor (INR ₹)")
        if vendor_spend_inr:
            sorted_spend = sorted(vendor_spend_inr.items(), key=lambda kv: kv[1], reverse=True)
            spend_df = pd.DataFrame(sorted_spend, columns=["Vendor", "Spend (INR)"])
            spend_df["VendorLabel"] = spend_df["Vendor"].apply(
                lambda v: v if len(str(v)) <= 22 else str(v)[:20] + "…"
            )
            fig_bar = px.bar(
                spend_df,
                x="Spend (INR)",
                y="VendorLabel",
                orientation="h",
                text_auto=".2s",
                hover_data={"Vendor": True, "VendorLabel": False, "Spend (INR)": ":,.2f"},
                color_discrete_sequence=[chart_color("bar", theme)],
            )
            fig_bar.update_layout(
                yaxis=dict(automargin=True, title=None),
                xaxis=dict(title="Approved spend (INR ₹)"),
            )
            style_figure(fig_bar, theme, height=300)
            st.plotly_chart(
                fig_bar,
                width="stretch",
                theme=None,
                config={"displayModeBar": False, "responsive": True},
            )
        else:
            st.info("No approved invoice spend recorded yet.")

    st.markdown("---")

    # 4. Recent processing activity (bounded to 10 rows)
    st.subheader("Recent Processing Runs")
    recent_invoices = (
        session.execute(
            select(Invoice)
            .options(joinedload(Invoice.vendor))
            .order_by(Invoice.created_at.desc(), Invoice.id.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )

    if recent_invoices:
        rows = []
        for inv in recent_invoices:
            inr_eq = convert_to_inr(inv.total_amount, inv.currency or "USD") if inv.total_amount is not None else None
            rows.append(
                {
                    "ID": inv.id,
                    "Decision": invoice_display_status(
                        inv.final_decision, inv.processing_status, inv.manual_override_decision
                    ),
                    "Vendor": inv.vendor.legal_name if inv.vendor else "Unknown / Unmatched",
                    "Invoice #": inv.invoice_number or "N/A",
                    "Native Amount": (
                        f"{inv.currency or 'Unconfirmed'} {inv.total_amount:,.2f}"
                        if inv.total_amount is not None
                        else "N/A"
                    ),
                    "Amount (INR ₹)": format_inr(inr_eq),
                    "Confidence": f"{(inv.extraction_confidence or 0) * 100:.0f}%",
                    "Filename": inv.original_filename,
                    "Processed (UTC)": inv.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        render_table(
            rows,
            [
                "ID",
                "Decision",
                "Vendor",
                "Invoice #",
                "Native Amount",
                "Amount (INR ₹)",
                "Confidence",
                "Filename",
                "Processed (UTC)",
            ],
            caption="Ten most recently processed invoices (with INR converted amounts)",
            column_kinds={"Decision": "status"},
            numeric_columns=["ID", "Native Amount", "Amount (INR ₹)", "Confidence"],
        )
    else:
        st.caption("No invoices processed yet. Navigate to 'Upload Invoice' to begin.")
