"""
Streamlit Vendors & Purchase Orders Management View.

Allows onboarding new vendors, toggling vendor approval status, issuing new
Purchase Orders with multi-row line items in Indian Rupees (INR / ₹), US Dollars
(USD / $), or any supported currency, and testing the probabilistic Vendor & PO
matching engine with live confidence scores.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config.rules import rules_config
from app.core.exceptions import APException
from app.core.schemas import NormalizedInvoice, NormalizedLineItem
from app.database.entities import PurchaseOrder, Vendor
from app.database.procurement_service import (
    create_purchase_order,
    create_vendor,
    set_vendor_approval,
)
from app.processing.currency import (
    SUPPORTED_CURRENCIES,
    convert_to_inr,
    format_inr,
    format_native_and_inr,
    get_usd_to_inr_rate,
)
from app.processing.normalizer import normalize_identifier, normalize_vendor_name
from app.processing.po_matcher import POMatcher
from app.processing.vendor_matcher import VendorMatcher
from app.ui.components import render_kpis, render_status_banner, render_table
from app.ui.presentation import Kpi

COUNTRY_CHOICES: tuple[tuple[str, str], ...] = (
    ("IND", "IND — India (₹ INR)"),
    ("USA", "USA — United States ($ USD)"),
    ("UK", "UK — United Kingdom (£ GBP)"),
    ("DEU", "DEU — Eurozone (€ EUR)"),
    ("CAN", "CAN — Canada (CA$ CAD)"),
    ("AUS", "AUS — Australia (A$ AUD)"),
    ("SGP", "SGP — Singapore (S$ SGD)"),
    ("ARE", "ARE — UAE (AED)"),
    ("HKG", "HKG — Hong Kong (HK$ HKD)"),
    ("NZL", "NZL — New Zealand (NZ$ NZD)"),
    ("JPN", "JPN — Japan (¥ JPY)"),
    ("CHN", "CHN — China (¥ CNY)"),
)

UNIT_CHOICES: tuple[str, ...] = ("EA", "PAIR", "BOX", "HRS", "KG", "LIC", "SET", "LOT")


def render_procurement_view(session: Session) -> None:
    st.markdown("## 🏢 Vendors & Purchase Orders Management")
    st.caption(
        "Onboard vendors, manage approval status, issue Purchase Orders in "
        f"INR (₹) or USD ($), and inspect probabilistic matching confidence scores "
        f"(active FX rate: 1 USD = ₹{get_usd_to_inr_rate():,.2f} INR)."
    )

    notice = st.session_state.pop("_procurement_notice", None)
    if notice:
        kind, msg = notice
        getattr(st, kind)(msg)

    vendors = (
        session.execute(
            select(Vendor)
            .options(selectinload(Vendor.purchase_orders))
            .order_by(Vendor.legal_name.asc(), Vendor.id.asc())
        )
        .scalars()
        .unique()
        .all()
    )
    purchase_orders = (
        session.execute(
            select(PurchaseOrder)
            .options(joinedload(PurchaseOrder.vendor), selectinload(PurchaseOrder.line_items))
            .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
        )
        .scalars()
        .unique()
        .all()
    )

    approved_vendors = sum(1 for v in vendors if v.is_approved)
    open_pos = [po for po in purchase_orders if po.status == "OPEN"]
    open_balance_inr = sum(
        (convert_to_inr(po.remaining_balance, po.currency) or 0.0) for po in open_pos
    )

    render_kpis(
        [
            Kpi("Registered Vendors", f"{len(vendors):,}", f"{approved_vendors} approved", "info"),
            Kpi("Total Purchase Orders", f"{len(purchase_orders):,}", f"{len(open_pos)} open POs", "success"),
            Kpi(
                "Open PO Balance (INR)",
                format_inr(open_balance_inr),
                f"Converted at 1 USD = ₹{get_usd_to_inr_rate():,.2f}",
                "info",
            ),
            Kpi(
                "Probabilistic Thresholds",
                f"Vendor {rules_config.matching.VENDOR_HIGH_CONFIDENCE_THRESHOLD * 100:.0f}% · PO {rules_config.matching.PO_SIMILARITY_THRESHOLD * 100:.0f}%",
                "Multi-signal confidence cutoffs",
                "neutral",
            ),
        ],
        label="Procurement summary metrics",
    )

    tab_vendors, tab_pos, tab_sim = st.tabs(
        [
            "🏢 Vendors Directory & Onboarding",
            "📑 Purchase Orders & Line Items",
            "🎯 Probabilistic Matcher & Confidence Simulator",
        ]
    )

    with tab_vendors:
        _render_vendors_tab(session, vendors)

    with tab_pos:
        _render_purchase_orders_tab(session, vendors, purchase_orders)

    with tab_sim:
        _render_probabilistic_simulator_tab(session)


def _render_vendors_tab(session: Session, vendors: Sequence[Vendor]) -> None:
    st.subheader("Add New Vendor")
    st.caption(
        "The engine runs a probabilistic similarity check across normalized company tokens, "
        "abbreviations (e.g., 'Ind.' → 'Industrial', 'Pvt. Ltd.'), and Tax IDs/GSTINs before registering."
    )
    country_codes = [c[0] for c in COUNTRY_CHOICES]
    country_labels = dict(COUNTRY_CHOICES)

    with st.form("create_vendor_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            legal_name = st.text_input(
                "Vendor Legal Company Name *",
                placeholder="e.g. Bharat Industrial Components Pvt. Ltd.",
            )
            tax_id = st.text_input(
                "Tax Registration ID / GSTIN / EIN",
                placeholder="e.g. 27AABCU9603R1ZM or IN-GST-5011",
            )
            country = st.selectbox(
                "Country",
                options=country_codes,
                index=0,
                format_func=lambda code: country_labels.get(code, code),
            )
        with c2:
            address = st.text_input(
                "Registered Address",
                placeholder="e.g. Plot 42, MIDC Andheri East, Mumbai, MH 400093",
            )
            bank_account = st.text_input(
                "Bank Account / IFSC Identifier",
                placeholder="e.g. IN-HDFC-0001829",
            )
            is_approved = st.checkbox(
                "Approved / Active Vendor (eligible for invoice payment)",
                value=True,
            )
            allow_similar = st.checkbox(
                "Confirm creation even if a probabilistically similar vendor name already exists",
                value=False,
                help="By default, the probabilistic model warns you if an existing vendor has >=85% confidence similarity under a different formatting.",
            )

        submit_vendor = st.form_submit_button("➕ Register Vendor", type="primary")
        if submit_vendor:
            try:
                created = create_vendor(
                    session,
                    legal_name=legal_name,
                    tax_id=tax_id,
                    country=country,
                    address=address,
                    bank_account=bank_account,
                    is_approved=is_approved,
                    allow_similar=allow_similar,
                )
                session.commit()
                st.session_state["_procurement_notice"] = (
                    "success",
                    f"Vendor '{created.legal_name}' (ID #{created.id}, normalized: '{created.normalized_name}') registered successfully.",
                )
                st.rerun()
            except APException as exc:
                session.rollback()
                st.warning(str(exc))

    if vendors:
        with st.expander("Update existing vendor approval status", expanded=False):
            u_col1, u_col2, u_col3 = st.columns([2.2, 1.2, 1], vertical_alignment="bottom")
            with u_col1:
                target_vid = st.selectbox(
                    "Select Vendor",
                    options=[v.id for v in vendors],
                    format_func=lambda vid: next(
                        (
                            f"#{v.id} · {v.legal_name} ({'Approved' if v.is_approved else 'Unapproved'})"
                            for v in vendors
                            if v.id == vid
                        ),
                        f"Vendor #{vid}",
                    ),
                    key="toggle_vendor_id",
                )
            selected_vendor_obj = next((v for v in vendors if v.id == target_vid), None)
            with u_col2:
                new_approval = st.selectbox(
                    "New Status",
                    options=[True, False],
                    index=0 if (selected_vendor_obj and selected_vendor_obj.is_approved) else 1,
                    format_func=lambda val: "✓ Approved / Active" if val else "✕ Unapproved / Blocked",
                    key="toggle_vendor_status_val",
                )
            with u_col3:
                if st.button("Update Status", width="stretch", key="btn_update_vendor_status"):
                    try:
                        updated = set_vendor_approval(
                            session, vendor_id=target_vid, is_approved=new_approval
                        )
                        session.commit()
                        st.session_state["_procurement_notice"] = (
                            "success",
                            f"Updated '{updated.legal_name}' to {'Approved' if updated.is_approved else 'Unapproved'}.",
                        )
                        st.rerun()
                    except APException as exc:
                        session.rollback()
                        st.error(str(exc))

    st.markdown("---")
    st.subheader(f"Registered Vendors ({len(vendors)})")
    vendor_rows = []
    for v in vendors:
        open_po_cnt = sum(1 for p in (v.purchase_orders or []) if p.status == "OPEN")
        vendor_rows.append(
            {
                "ID": v.id,
                "Approval": "APPROVED" if v.is_approved else "BLOCKED",
                "Legal Name": v.legal_name,
                "Normalized Tokens": v.normalized_name,
                "Tax ID / GSTIN": v.tax_id or "—",
                "Country": v.country,
                "Open POs": open_po_cnt,
                "Bank Account": v.bank_account or "—",
                "Address": v.address or "—",
            }
        )
    render_table(
        vendor_rows,
        [
            "ID",
            "Approval",
            "Legal Name",
            "Normalized Tokens",
            "Tax ID / GSTIN",
            "Country",
            "Open POs",
            "Bank Account",
            "Address",
        ],
        caption="Registered procurement vendors",
        column_kinds={"Approval": "status"},
        wrap_columns=["Address", "Legal Name"],
        numeric_columns=["ID", "Open POs"],
        empty_message="No vendors registered yet.",
    )


def _render_purchase_orders_tab(
    session: Session, vendors: Sequence[Vendor], purchase_orders: Sequence[PurchaseOrder]
) -> None:
    st.subheader("Create New Purchase Order")
    st.caption(
        "During invoice matching, PO references are matched probabilistically across formatting "
        "variants (e.g., 'PO# 2026/01' ↔ 'PO-2026-001'), leading zeros, OCR look-alikes, and "
        "multi-signal vendor/amount/line-item similarity."
    )
    if not vendors:
        st.warning("Register at least one vendor in the 'Vendors Directory & Onboarding' tab before creating a PO.")
        return

    line_count = int(st.session_state.get("_new_po_line_count", 2))
    lc_col1, lc_col2, lc_col3 = st.columns([1.2, 1.2, 2.6], vertical_alignment="center")
    with lc_col1:
        if st.button("➕ Add Line Item Row", width="stretch", disabled=(line_count >= 10)):
            st.session_state["_new_po_line_count"] = min(10, line_count + 1)
            st.rerun()
    with lc_col2:
        if st.button("➖ Remove Last Row", width="stretch", disabled=(line_count <= 1)):
            st.session_state["_new_po_line_count"] = max(1, line_count - 1)
            st.rerun()
    with lc_col3:
        st.caption(f"Configuring **{line_count}** line item row(s) for the new Purchase Order.")

    today_str = date.today().isoformat()
    default_expiry = (date.today() + timedelta(days=365)).isoformat()

    with st.form("create_po_form"):
        p_col1, p_col2, p_col3 = st.columns([2, 1.2, 1])
        with p_col1:
            po_vendor_id = st.selectbox(
                "Vendor *",
                options=[v.id for v in vendors],
                format_func=lambda vid: next(
                    (f"{v.legal_name} ({v.country} · ID #{v.id})" for v in vendors if v.id == vid),
                    f"Vendor #{vid}",
                ),
            )
        with p_col2:
            po_number = st.text_input(
                "PO Reference Number *",
                placeholder="e.g. PO-2026-IND-001",
            )
        with p_col3:
            po_currency = st.selectbox(
                "PO Currency *",
                options=list(SUPPORTED_CURRENCIES),
                index=0,  # INR first
            )

        d_col1, d_col2, d_col3 = st.columns([1.2, 1.2, 1.6])
        with d_col1:
            po_date_val = st.text_input("PO Date (YYYY-MM-DD)", value=today_str)
        with d_col2:
            po_expiry_val = st.text_input("Expiry Date (YYYY-MM-DD)", value=default_expiry)
        with d_col3:
            po_budget_override = st.number_input(
                "Optional Total PO Ceiling / Budget (0 = auto-sum line items)",
                min_value=0.0,
                value=0.0,
                step=100.0,
                format="%.2f",
                help="Leave at 0.00 to set Total PO Amount equal to the sum of line items, or set a higher ceiling to cover tax/split deliveries.",
            )

        st.markdown("##### Purchase Order Line Items")
        line_inputs: list[dict[str, object]] = []
        for idx in range(1, line_count + 1):
            l1, l2, l3, l4 = st.columns([2.8, 1.0, 0.9, 1.3])
            with l1:
                desc = st.text_input(
                    f"Line #{idx} Description",
                    placeholder="e.g. Industrial Safety Helmets",
                    key=f"new_po_desc_{idx}",
                )
            with l2:
                qty = st.number_input(
                    f"Line #{idx} Qty",
                    min_value=0.01,
                    value=10.0,
                    step=1.0,
                    format="%.2f",
                    key=f"new_po_qty_{idx}",
                )
            with l3:
                unit = st.selectbox(
                    f"Line #{idx} Unit",
                    options=list(UNIT_CHOICES),
                    index=0,
                    key=f"new_po_unit_{idx}",
                )
            with l4:
                uprice = st.number_input(
                    f"Line #{idx} Unit Price ({po_currency})",
                    min_value=0.0,
                    value=500.0 if po_currency == "INR" else 50.0,
                    step=10.0,
                    format="%.2f",
                    key=f"new_po_price_{idx}",
                )
            line_inputs.append(
                {
                    "description": desc,
                    "quantity": qty,
                    "unit": unit,
                    "unit_price": uprice,
                }
            )

        allow_similar_po = st.checkbox(
            "Confirm creation even if a differently formatted PO with >=90% reference similarity exists",
            value=False,
        )

        submit_po = st.form_submit_button("➕ Issue Purchase Order", type="primary")
        if submit_po:
            try:
                created_po = create_purchase_order(
                    session,
                    vendor_id=po_vendor_id,
                    po_number=po_number,
                    currency=po_currency,
                    po_date=po_date_val,
                    expiry_date=po_expiry_val,
                    line_items=line_inputs,
                    total_amount_override=po_budget_override if po_budget_override > 0 else None,
                    status="OPEN",
                    allow_similar=allow_similar_po,
                )
                session.commit()
                st.session_state["_procurement_notice"] = (
                    "success",
                    f"Purchase Order '{created_po.po_number}' created for "
                    f"{format_native_and_inr(created_po.total_amount, created_po.currency)}.",
                )
                st.rerun()
            except APException as exc:
                session.rollback()
                st.warning(str(exc))

    st.markdown("---")
    st.subheader(f"Purchase Orders Registry ({len(purchase_orders)})")

    po_rows = []
    for po in purchase_orders:
        rem_inr = convert_to_inr(po.remaining_balance, po.currency)
        po_rows.append(
            {
                "ID": po.id,
                "PO Number": po.po_number,
                "Vendor": po.vendor.legal_name if po.vendor else f"Vendor #{po.vendor_id}",
                "PO Status": po.status,
                "Currency": po.currency,
                "Total Amount": f"{po.currency} {po.total_amount:,.2f}",
                "Billed": f"{po.currency} {po.billed_amount:,.2f}",
                "Remaining Balance": f"{po.currency} {po.remaining_balance:,.2f}",
                "Remaining (INR ₹)": format_inr(rem_inr),
                "Lines": len(po.line_items or []),
                "PO Date": po.po_date,
            }
        )

    render_table(
        po_rows,
        [
            "ID",
            "PO Number",
            "Vendor",
            "PO Status",
            "Currency",
            "Total Amount",
            "Billed",
            "Remaining Balance",
            "Remaining (INR ₹)",
            "Lines",
            "PO Date",
        ],
        caption="All purchase orders with native and INR-converted balances",
        numeric_columns=["ID", "Total Amount", "Billed", "Remaining Balance", "Remaining (INR ₹)", "Lines"],
        wrap_columns=["Vendor"],
        empty_message="No purchase orders recorded yet.",
    )

    with st.expander("Inspect Purchase Order Line Items", expanded=False):
        for po in purchase_orders:
            header = (
                f"{po.po_number} · {po.vendor.legal_name if po.vendor else ''} · "
                f"{po.status} · Remaining: {format_native_and_inr(po.remaining_balance, po.currency)}"
            )
            with st.expander(header, expanded=False):
                if po.line_items:
                    li_rows = [
                        {
                            "Line #": li.line_number,
                            "Description": li.description,
                            "Quantity": f"{li.quantity:g}",
                            "Unit": li.unit,
                            "Unit Price": f"{po.currency} {li.unit_price:,.2f}",
                            "Line Total": f"{po.currency} {li.line_total:,.2f}",
                            "Line Total (INR ₹)": format_inr(convert_to_inr(li.line_total, po.currency)),
                        }
                        for li in po.line_items
                    ]
                    render_table(
                        li_rows,
                        [
                            "Line #",
                            "Description",
                            "Quantity",
                            "Unit",
                            "Unit Price",
                            "Line Total",
                            "Line Total (INR ₹)",
                        ],
                        caption=f"Line items for {po.po_number}",
                        numeric_columns=["Line #", "Quantity", "Unit Price", "Line Total", "Line Total (INR ₹)"],
                        wrap_columns=["Description"],
                    )
                else:
                    st.caption("No line items attached to this Purchase Order.")


def _render_probabilistic_simulator_tab(session: Session) -> None:
    st.subheader("🎯 Test Probabilistic Vendor & PO Matching (Confidence Simulator)")
    st.write(
        "Vendors frequently format company names, tax IDs, and PO numbers differently on invoices "
        "(e.g., `M/s Acme Ind. Supply` instead of `Acme Industrial Supplies Inc.`, or `P.O. #2026/01` "
        "instead of `PO-2026-001`). Enter any differently formatted invoice fields below to inspect "
        "the probabilistic confidence scores and signal breakdowns."
    )

    s1, s2, s3 = st.columns(3)
    with s1:
        sim_vendor = st.text_input(
            "Invoice Vendor Name (try abbreviations / reordered words)",
            value="M/s Acme Ind. Supply",
            key="sim_vendor_name",
        )
        sim_tax = st.text_input(
            "Invoice Tax ID / GSTIN (optional)",
            value="",
            placeholder="e.g. USTAX 1011",
            key="sim_vendor_tax",
        )
    with s2:
        sim_po = st.text_input(
            "Invoice PO Reference (try different delimiters / leading zeros / leave blank)",
            value="P.O. #2026/01",
            key="sim_po_ref",
        )
        sim_line = st.text_input(
            "Sample Line Item Description",
            value="Ind. Safety Helmets",
            key="sim_line_desc",
        )
    with s3:
        sim_curr = st.selectbox(
            "Invoice Currency",
            options=list(SUPPORTED_CURRENCIES),
            index=0,
            key="sim_currency",
        )
        sim_amount = st.number_input(
            f"Invoice Total Amount ({sim_curr})",
            min_value=0.0,
            value=113400.0 if sim_curr == "INR" else 1350.0,
            step=100.0,
            format="%.2f",
            key="sim_amount",
        )

    sim_invoice = NormalizedInvoice(
        vendor_name=sim_vendor,
        normalized_vendor_name=normalize_vendor_name(sim_vendor),
        vendor_tax_id=sim_tax.strip() or None,
        po_reference=sim_po.strip() or None,
        normalized_po_reference=normalize_identifier(sim_po),
        currency=sim_curr,
        total_amount=sim_amount,
        line_items=[
            NormalizedLineItem(
                description=sim_line,
                quantity=1.0,
                unit="EA",
                unit_price=sim_amount,
                line_total=sim_amount,
            )
        ]
        if sim_line.strip()
        else [],
    )

    v_matcher = VendorMatcher(session)
    matched_v, v_cands, v_score, v_status = v_matcher.match_vendor(sim_invoice)

    p_matcher = POMatcher(session)
    matched_p, p_cands, p_score, p_status = p_matcher.match_po(sim_invoice, matched_v)

    v_tone = "APPROVED" if v_status == "CONFIDENT_MATCH" else ("NEEDS_REVIEW" if v_status == "AMBIGUOUS_MATCH" else "BLOCKED")
    p_tone = "APPROVED" if p_status == "CONFIDENT_MATCH" else ("NEEDS_REVIEW" if p_status == "AMBIGUOUS_MATCH" else "BLOCKED")

    col_res1, col_res2 = st.columns(2)
    with col_res1:
        st.markdown("#### Vendor Probabilistic Match")
        render_status_banner(
            v_tone,
            title=f"{v_status} · Confidence {v_score * 100:.1f}%",
            body=(
                f"Best candidate: {matched_v.legal_name} (ID #{matched_v.id})"
                if matched_v
                else "No vendor above the minimum review threshold (60%)."
            ),
            size="md",
        )
        v_cand_rows = [
            {
                "Rank": idx,
                "Vendor": c.legal_name,
                "Confidence": f"{c.score * 100:.1f}%",
                "Match Signal": c.match_type,
                "Breakdown": c.explanation,
            }
            for idx, c in enumerate(v_cands, start=1)
        ]
        render_table(
            v_cand_rows,
            ["Rank", "Vendor", "Confidence", "Match Signal", "Breakdown"],
            caption="Ranked probabilistic vendor candidates",
            wrap_columns=["Breakdown", "Vendor"],
            numeric_columns=["Rank", "Confidence"],
            empty_message="No vendor candidates scored >= 60%.",
        )

    with col_res2:
        st.markdown("#### Purchase Order Probabilistic Match")
        render_status_banner(
            p_tone,
            title=f"{p_status} · Confidence {p_score * 100:.1f}%",
            body=(
                f"Best candidate: {matched_p.po_number} ({format_native_and_inr(matched_p.remaining_balance, matched_p.currency)} remaining)"
                if matched_p
                else "No Purchase Order candidate identified."
            ),
            size="md",
        )
        p_cand_rows = [
            {
                "Rank": idx,
                "PO Number": c.po_number,
                "Confidence": f"{c.score * 100:.1f}%",
                "Remaining Balance": format_native_and_inr(c.remaining_balance, c.currency),
                "Breakdown": c.explanation,
            }
            for idx, c in enumerate(p_cands, start=1)
        ]
        render_table(
            p_cand_rows,
            ["Rank", "PO Number", "Confidence", "Remaining Balance", "Breakdown"],
            caption="Ranked probabilistic PO candidates",
            wrap_columns=["Breakdown"],
            numeric_columns=["Rank", "Confidence", "Remaining Balance"],
            empty_message="No Purchase Order candidates scored >= 50%.",
        )
