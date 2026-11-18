"""
Streamlit Invoice Details & Decision Page:
Deep inspection of extracted data, 3-way matching, validation checks, audit trail,
and human-in-the-loop overrides.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ui as ui_config
from app.database.entities import (
    AuditLog,
    Invoice,
    PurchaseOrder,
    ValidationResultEntity,
    Vendor,
)
from app.database.queries import (
    InvoiceFilters,
    count_invoices,
    get_invoice,
    latest_run_for_invoice,
    paginate_invoices,
)
from app.ui.components import (
    render_kpis,
    render_pager,
    render_status_banner,
    render_table,
    store_page,
    sync_pagination,
)
from app.ui.document_preview import (
    clamp_page,
    image_html,
    inspect_document,
    normalise_rotation,
    normalise_zoom,
    render_image_png,
    render_pdf_page_annotated,
)
from app.processing.currency import (
    SUPPORTED_CURRENCIES,
    convert_to_inr,
    format_inr,
    format_native_and_inr,
)
from app.processing.exporter import (
    to_audit_json,
    to_quickbooks_bills_csv,
    to_xero_bills_csv,
)
from app.processing.probabilistic_matcher import (
    score_po_reference_similarity,
    score_vendor_similarity,
)
from app.ui.presentation import (
    FILTERABLE_STATUSES,
    Kpi,
    invoice_display_status,
    status_meta,
)
from app.workflow.orchestrator import WorkflowOrchestrator

PICKER_PREFIX = "details_picker"


@st.cache_data(ttl=300, max_entries=64, show_spinner=False)
def _cached_pdf_page(
    path_str: str,
    mtime_ns: int,
    page: int,
    zoom: int,
    rotation: int = 0,
    highlight_terms: tuple[str, ...] = (),
) -> tuple[bytes, int]:
    del mtime_ns
    return render_pdf_page_annotated(
        path_str, page, zoom, rotation=rotation, highlight_terms=highlight_terms
    )


@st.cache_data(ttl=300, max_entries=32, show_spinner=False)
def _cached_raster_image(path_str: str, mtime_ns: int, rotation: int = 0) -> bytes:
    del mtime_ns
    return render_image_png(path_str, rotation=rotation)


def _select_invoice_scalable(session: Session) -> Invoice | None:
    """Server-side searchable + paginated invoice selector (never calls .all())."""
    total_any = count_invoices(session, InvoiceFilters())
    if total_any == 0:
        st.info("No invoices processed yet. Upload an invoice to inspect.")
        return None

    with st.expander("🔎 Search or filter invoices to audit", expanded=False):
        f_col1, f_col2 = st.columns([2, 1])
        with f_col1:
            search_term = st.text_input(
                "Filter selector by invoice #, PO reference, or filename",
                placeholder="e.g. INV-2026, PO-2026, scenario_1…",
                key="details_search",
            )
        with f_col2:
            status_filter = st.selectbox(
                "Filter selector by status",
                options=[None, *FILTERABLE_STATUSES],
                format_func=lambda s: "All statuses"
                if s is None
                else f"{status_meta(s).symbol} {status_meta(s).label}",
                key="details_status_filter",
            )
        filters = InvoiceFilters(search=search_term or "", status=status_filter)
        req_page = sync_pagination(PICKER_PREFIX, filters.signature())
        page_invoices, page_info = paginate_invoices(
            session, filters, req_page, ui_config.DETAILS_PICKER_PAGE_SIZE
        )
        store_page(PICKER_PREFIX, page_info)
        render_pager(PICKER_PREFIX, page_info)

    # Ensure a directly-selected invoice (from History / Upload / Live View) stays
    # selectable even if it is not on the current picker page.
    preferred_id = st.session_state.get("selected_invoice_id")
    preferred_inv = get_invoice(session, preferred_id) if preferred_id is not None else None

    candidates: list[Invoice] = list(page_invoices)
    if preferred_inv is not None and all(inv.id != preferred_inv.id for inv in candidates):
        if not (filters.search.strip() or filters.status):
            candidates.insert(0, preferred_inv)
        elif not candidates:
            candidates.insert(0, preferred_inv)

    if not candidates:
        st.warning("No invoices match the selector filter above. Clear the filter to pick an invoice.")
        return None

    def _label(inv: Invoice) -> str:
        disp = invoice_display_status(
            inv.final_decision, inv.processing_status, inv.manual_override_decision
        )
        meta = status_meta(disp)
        amt = format_native_and_inr(inv.total_amount, inv.currency)
        return (
            f"#{inv.id} · {meta.symbol} {meta.label} · {inv.original_filename} "
            f"({inv.invoice_number or 'No Inv#'}) · {amt}"
        )

    inv_map = {inv.id: _label(inv) for inv in candidates}
    option_ids = list(inv_map.keys())
    default_idx = option_ids.index(preferred_id) if preferred_id in inv_map else 0

    selected_id = st.selectbox(
        f"Select Invoice to Audit (showing {len(option_ids)} of {page_info.total} matching)",
        options=option_ids,
        index=default_idx,
        format_func=lambda x: inv_map.get(x, f"Invoice #{x}"),
    )
    st.session_state["selected_invoice_id"] = selected_id
    return get_invoice(session, selected_id)


def _extract_highlight_candidates(invoice: Invoice) -> tuple[str, ...]:
    """Collect non-trivial field values from the invoice for visual PDF highlighting."""
    candidates: list[str] = []
    for val in (
        invoice.invoice_number,
        invoice.po_reference,
        invoice.invoice_date,
        invoice.due_date,
        f"{invoice.total_amount:.2f}" if invoice.total_amount is not None else None,
        invoice.vendor.legal_name if invoice.vendor else None,
    ):
        if val and len(str(val).strip()) >= 3:
            candidates.append(str(val).strip())
    return tuple(dict.fromkeys(candidates))


def _render_document_preview(invoice: Invoice) -> None:
    st.subheader("📄 Document Preview")
    doc_info = inspect_document(invoice.file_path)

    # Reset page to 1 and rotation to 0 when switching to a different invoice; clamp within bounds.
    if st.session_state.get("_preview_invoice_id") != invoice.id:
        st.session_state["_preview_invoice_id"] = invoice.id
        st.session_state["_preview_page"] = 1
        st.session_state["_preview_rotation"] = 0

    if doc_info.kind == "missing":
        st.info(doc_info.message)
        return
    if doc_info.kind == "invalid":
        st.warning(doc_info.message)
        return

    file_path = Path(invoice.file_path)
    try:
        mtime_ns = file_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0

    zoom_default = normalise_zoom(int(st.session_state.get("_preview_zoom", ui_config.DEFAULT_ZOOM)))
    rot_default = normalise_rotation(int(st.session_state.get("_preview_rotation", 0)))

    if doc_info.kind == "pdf":
        page_count = doc_info.page_count
        current_page = clamp_page(int(st.session_state.get("_preview_page", 1)), page_count)
        st.session_state["_preview_page"] = current_page

        ctrl_prev, ctrl_page, ctrl_next, ctrl_zoom, ctrl_rot = st.columns(
            [0.9, 1.35, 0.9, 1.1, 1.0], vertical_alignment="bottom"
        )
        with ctrl_prev:
            if st.button(
                "‹ Prev",
                key=f"pdf_prev_{invoice.id}",
                disabled=(current_page <= 1),
                width="stretch",
                help="Previous PDF page",
            ):
                st.session_state["_preview_page"] = clamp_page(current_page - 1, page_count)
                st.rerun()
        with ctrl_page:
            picked_page = st.selectbox(
                f"Page (1–{page_count})",
                options=list(range(1, page_count + 1)),
                index=current_page - 1,
                format_func=lambda p: f"Page {p} of {page_count}",
                key=f"pdf_page_sel_{invoice.id}_{current_page}",
            )
            if picked_page != current_page:
                st.session_state["_preview_page"] = clamp_page(picked_page, page_count)
                st.rerun()
        with ctrl_next:
            if st.button(
                "Next ›",
                key=f"pdf_next_{invoice.id}",
                disabled=(current_page >= page_count),
                width="stretch",
                help="Next PDF page",
            ):
                st.session_state["_preview_page"] = clamp_page(current_page + 1, page_count)
                st.rerun()
        with ctrl_zoom:
            zoom = st.selectbox(
                "Zoom",
                options=list(ui_config.ZOOM_LEVELS),
                index=list(ui_config.ZOOM_LEVELS).index(zoom_default),
                format_func=lambda z: f"{z}%",
                key=f"pdf_zoom_{invoice.id}",
            )
            st.session_state["_preview_zoom"] = zoom
        with ctrl_rot:
            rotation = st.selectbox(
                "Rotate",
                options=list(ui_config.ROTATION_DEGREES),
                index=list(ui_config.ROTATION_DEGREES).index(rot_default),
                format_func=lambda d: f"↻ {d}°",
                key=f"pdf_rot_{invoice.id}",
            )
            st.session_state["_preview_rotation"] = rotation

        hl_col1, hl_col2 = st.columns([1.2, 1.8], vertical_alignment="center")
        with hl_col1:
            highlight_fields = st.checkbox(
                "Highlight extracted fields",
                value=False,
                key=f"pdf_hl_fields_{invoice.id}",
                help="Draw translucent bounding boxes around extracted invoice values found on this page.",
            )
        with hl_col2:
            search_on_page = st.text_input(
                "Find text on page",
                placeholder="Highlight text on page…",
                label_visibility="collapsed",
                key=f"pdf_search_{invoice.id}",
            )

        terms: list[str] = []
        if highlight_fields:
            terms.extend(_extract_highlight_candidates(invoice))
        if search_on_page and search_on_page.strip():
            terms.append(search_on_page.strip())

        try:
            png_bytes, match_count = _cached_pdf_page(
                str(file_path), mtime_ns, current_page, zoom, rotation, tuple(terms)
            )
            alt = f"{file_path.name} — page {current_page} of {page_count} at {zoom}% zoom"
            st.html(image_html(png_bytes, zoom, alt=alt))
            hl_note = f" · Highlighted **{match_count}** match(es)" if terms else ""
            rot_note = f" · Rotated **{rotation}°**" if rotation else ""
            st.caption(
                f"Showing **Page {current_page} of {page_count}** · Zoom **{zoom}%**"
                f"{rot_note}{hl_note} · `{file_path.name}`"
            )
        except Exception as exc:
            st.warning(f"Could not render page {current_page} of `{file_path.name}`: {exc}")
    else:
        # Raster image upload: show image clearly without PDF-only page controls, plus rotation.
        r_col1, r_col2 = st.columns([2.5, 1], vertical_alignment="bottom")
        with r_col1:
            st.caption(f"Raster image document (`{file_path.name}`) — single image preview.")
        with r_col2:
            rotation = st.selectbox(
                "Rotate image",
                options=list(ui_config.ROTATION_DEGREES),
                index=list(ui_config.ROTATION_DEGREES).index(rot_default),
                format_func=lambda d: f"↻ {d}°",
                key=f"img_rot_{invoice.id}",
            )
            st.session_state["_preview_rotation"] = rotation
        try:
            png_bytes = _cached_raster_image(str(file_path), mtime_ns, rotation=rotation)
            st.html(image_html(png_bytes, 100, alt=f"Uploaded invoice image {file_path.name}"))
        except Exception as exc:
            st.warning(f"Could not render uploaded image `{file_path.name}`: {exc}")


def render_invoice_details(session: Session):
    st.markdown("## 🔍 Invoice Audit & Decision Workbench")

    invoice = _select_invoice_scalable(session)
    if not invoice:
        return

    latest_run = latest_run_for_invoice(session, invoice.id)
    effective_status = invoice_display_status(
        invoice.final_decision, invoice.processing_status, invoice.manual_override_decision
    )
    meta = status_meta(effective_status)

    st.markdown(f"### Invoice `{invoice.invoice_number or 'UNASSIGNED'}` · File `{invoice.original_filename}`")

    if invoice.manual_override_decision:
        render_status_banner(
            effective_status,
            title=f"Manual Override Active — {meta.label}",
            body=(
                f"Automated decision was {invoice.final_decision}, overridden to "
                f"{invoice.manual_override_decision} by {invoice.manual_override_by}."
            ),
        )
    elif effective_status == "FAILED":
        err_detail = (
            latest_run.error_message
            if latest_run and latest_run.error_message
            else "The processing pipeline terminated before completing all stages."
        )
        render_status_banner(
            "FAILED",
            title="Processing Run Failed — Needs Human Review",
            body=f"{err_detail} Automated decision fallback: {invoice.final_decision}.",
        )
    else:
        render_status_banner(
            effective_status,
            title=f"Decision: {meta.label}",
            body=meta.description,
        )

    exp_c1, exp_c2, exp_c3 = st.columns(3)
    with exp_c1:
        st.download_button(
            "⬇️ Export Xero Bills CSV",
            data=to_xero_bills_csv([invoice]),
            file_name=f"xero_invoice_{invoice.id}.csv",
            mime="text/csv",
            width="stretch",
        )
    with exp_c2:
        st.download_button(
            "⬇️ Export QuickBooks CSV",
            data=to_quickbooks_bills_csv([invoice]),
            file_name=f"quickbooks_invoice_{invoice.id}.csv",
            mime="text/csv",
            width="stretch",
        )
    with exp_c3:
        st.download_button(
            "⬇️ Export Audit JSON",
            data=to_audit_json([invoice]),
            file_name=f"audit_invoice_{invoice.id}.json",
            mime="application/json",
            width="stretch",
        )

    st.markdown("---")

    # Document Preview + Extracted Data Layout (stacks on narrow screens)
    col_preview, col_fields = st.columns([1, 1])

    with col_preview:
        _render_document_preview(invoice)

    with col_fields:
        st.subheader("Extracted Structured Fields")

        extracted: dict[str, Any] = {}
        try:
            extracted = json.loads(invoice.extracted_data) if invoice.extracted_data else {}
        except Exception:
            extracted = {}

        def fld(key: str):
            obj = extracted.get(key) if isinstance(extracted, dict) else None
            if isinstance(obj, dict):
                conf = obj.get("confidence")
                conf_str = f"{conf * 100:.0f}%" if isinstance(conf, (int, float)) else "—"
                ev = obj.get("evidence") or "—"
                return obj.get("value"), conf_str, ev
            return None, "—", "—"

        provider_note = "Demo / rule-based extractor" if extracted.get("is_mock", True) else "LLM extractor"
        st.caption(f"Confidence and evidence are reported per field by the {provider_note}.")

        field_specs = [
            ("Vendor Name", "vendor_name", None),
            ("Invoice Number", "invoice_number", None),
            ("Invoice Date", "invoice_date", None),
            ("Payment Due Date", "due_date", None),
            ("Paid Date", "paid_date", None),
            ("PO Reference", "po_reference", None),
            ("Currency", "currency", None),
            ("Tax ID", "vendor_tax_id", None),
            ("Subtotal", "subtotal", "money"),
            ("Tax Amount", "tax_amount", "money"),
            ("Total Amount", "total_amount", "money"),
        ]

        fields_data = []
        for label, key, kind in field_specs:
            value, conf_str, ev = fld(key)
            ocr_confidence = extracted.get("ocr_field_confidences", {}).get(key)
            ocr_confidence_str = f"{ocr_confidence:.0f}%" if isinstance(ocr_confidence, (int, float)) else "—"
            if value is None:
                shown = "— (currency ambiguous)" if key == "currency" and ev != "—" else "— (not found)"
            elif kind == "money":
                try:
                    currency_field = extracted.get("currency")
                    currency_value = currency_field.get("value") if isinstance(currency_field, dict) else None
                    currency_code = (
                        currency_value
                        if currency_value in set(SUPPORTED_CURRENCIES)
                        else (invoice.currency if invoice.currency in set(SUPPORTED_CURRENCIES) else None)
                    )
                    shown = (
                        format_native_and_inr(float(value), currency_code)
                        if currency_code
                        else f"{float(value):,.2f} (currency unconfirmed)"
                    )
                except (TypeError, ValueError):
                    shown = str(value)
            else:
                shown = str(value)
            fields_data.append(
                {
                    "Field": label,
                    "Value": shown,
                    "Conf.": conf_str,
                    "OCR Conf.": ocr_confidence_str,
                    "Evidence": str(ev),
                }
            )

        fields_data.append(
            {
                "Field": "SHA-256 Hash",
                "Value": invoice.file_hash[:16] + "…",
                "Conf.": "100%",
                "OCR Conf.": "—",
                "Evidence": "binary file hash",
            }
        )

        render_table(
            fields_data,
            ["Field", "Value", "Conf.", "OCR Conf.", "Evidence"],
            caption="Extracted invoice fields with per-field confidence and verbatim evidence",
            wrap_columns=["Evidence", "Value"],
            numeric_columns=["Conf.", "OCR Conf."],
        )

        if extracted.get("overall_confidence") is not None:
            st.caption(f"Overall extraction confidence: **{extracted['overall_confidence'] * 100:.0f}%**")
        if extracted.get("ocr_confidence") is not None:
            st.caption(f"OCR word confidence: **{extracted['ocr_confidence']:.1f}%**")
        if extracted.get("raw_text"):
            with st.expander("OCR / source text sent to the extractor"):
                st.text(extracted["raw_text"])

    if invoice.manual_override_at:
        st.markdown("#### Reviewer-accepted values now in effect")
        final_vendor = invoice.vendor.legal_name if invoice.vendor else "Unmatched"
        final_po = invoice.matched_po.po_number if invoice.matched_po else "Unlinked"
        render_kpis(
            [
                Kpi("Vendor", final_vendor),
                Kpi("Invoice #", invoice.invoice_number or "—"),
                Kpi("Purchase order", final_po),
                Kpi("Currency", invoice.currency or "Unconfirmed"),
                Kpi(
                    "Total",
                    f"{invoice.currency or 'Currency unconfirmed'} {invoice.total_amount:,.2f}"
                    if invoice.total_amount is not None
                    else "—",
                ),
            ],
            label="Reviewer-accepted values",
        )

    st.markdown("---")

    # 3-Way Match: Vendor, PO, Line Items
    st.subheader("3-Way Procurement Comparison")
    col_v, col_p = st.columns(2)

    with col_v:
        st.markdown("#### Matched Vendor")
        if invoice.vendor:
            v = invoice.vendor
            raw_v_name, _, _ = fld("vendor_name")
            raw_v_tax, _, _ = fld("vendor_tax_id")
            v_bd = score_vendor_similarity(
                query_name=str(raw_v_name or v.legal_name),
                vendor_legal_name=v.legal_name,
                vendor_normalized_name=v.normalized_name,
                query_tax_id=str(raw_v_tax) if raw_v_tax else None,
                vendor_tax_id=v.tax_id,
            )
            st.success(f"**{v.legal_name}** · Probabilistic Confidence **{v_bd.score * 100:.1f}%**")
            st.caption(f"Signal breakdown: {v_bd.explanation}")
            st.write(f"- **Tax ID:** `{v.tax_id}`")
            st.write(f"- **Address:** {v.address}")
            st.write(f"- **Approval Status:** {'✅ Approved' if v.is_approved else '❌ Unapproved'}")
        else:
            st.error("No registered vendor matched.")

    with col_p:
        st.markdown("#### Matched Purchase Order")
        if invoice.matched_po:
            p = invoice.matched_po
            po_bd = score_po_reference_similarity(invoice.po_reference, p.po_number)
            po_conf_label = (
                f" · Probabilistic Confidence **{po_bd.score * 100:.1f}%**"
                if po_bd.score > 0
                else " · Linked via vendor/reviewer match"
            )
            st.success(f"**PO: {p.po_number}**{po_conf_label}")
            if po_bd.score > 0:
                st.caption(f"Signal breakdown: {po_bd.explanation}")
            st.write(f"- **Total PO Amount:** `{format_native_and_inr(p.total_amount, p.currency)}`")
            st.write(f"- **Previously Billed:** `{format_native_and_inr(p.billed_amount, p.currency)}`")
            st.write(f"- **Remaining Balance:** `{format_native_and_inr(p.remaining_balance, p.currency)}`")
            st.write(f"- **PO Status:** `{p.status}`")
        else:
            st.warning("No Purchase Order linked to this invoice.")

    # Line Item Table
    st.markdown("#### Line Items & PO Comparison")
    if invoice.line_items:
        items_rows = []
        for line in invoice.line_items:
            po_match_desc = line.matched_po_line.description if line.matched_po_line else "Unmatched"
            items_rows.append(
                {
                    "Line #": line.line_number,
                    "Description": line.description,
                    "Qty": line.quantity,
                    "Unit Price": f"{invoice.currency + ' ' if invoice.currency else ''}{line.unit_price:,.2f}",
                    "Line Total": f"{invoice.currency + ' ' if invoice.currency else ''}{line.line_total:,.2f}",
                    "Matched PO Line": po_match_desc,
                }
            )
        render_table(
            items_rows,
            ["Line #", "Description", "Qty", "Unit Price", "Line Total", "Matched PO Line"],
            caption="Invoice line items compared with purchase order lines",
            wrap_columns=["Description", "Matched PO Line"],
            numeric_columns=["Line #", "Qty", "Unit Price", "Line Total"],
        )
    else:
        st.caption("No individual line items recorded.")

    st.markdown("---")

    # Deterministic Validation Rules Results Table
    st.subheader("Deterministic Validation Rules Results")
    validations = (
        session.execute(
            select(ValidationResultEntity)
            .where(ValidationResultEntity.invoice_id == invoice.id)
            .order_by(ValidationResultEntity.id.asc())
        )
        .scalars()
        .all()
    )

    if validations:
        v_rows = []
        for validation in validations:
            res_icon = (
                "✓ PASS"
                if validation.result == "PASS"
                else ("✕ FAIL" if validation.result == "FAIL" else "! WARN")
            )
            v_rows.append(
                {
                    "Rule ID": validation.rule_id,
                    "Rule Name": validation.rule_name,
                    "Category": validation.category,
                    "Result": res_icon,
                    "Severity": validation.severity,
                    "Expected": validation.expected_value or "—",
                    "Actual": validation.actual_value or "—",
                    "Explanation": validation.explanation,
                }
            )
        render_table(
            v_rows,
            ["Rule ID", "Rule Name", "Category", "Result", "Severity", "Expected", "Actual", "Explanation"],
            caption="Deterministic validation rule checks",
            wrap_columns=["Explanation", "Rule Name"],
        )
    else:
        st.caption("No validation rules recorded for this invoice.")

    st.markdown("---")

    # Human Review & Manual Override Section
    st.subheader("✍️ Human-in-the-Loop Review & Decision Override")
    st.caption(
        "Demo reviewer identity is entered below. Decision changes and corrections require a reason "
        "and are recorded with before/after values."
    )

    review_notice = st.session_state.pop("_review_notice", None)
    if review_notice:
        kind, message = review_notice
        getattr(st, kind)(message)

    override_choices = ["APPROVED", "NEEDS_REVIEW", "BLOCKED"]
    current_decision_choice = (
        invoice.manual_override_decision
        if invoice.manual_override_decision in override_choices
        else (invoice.final_decision if invoice.final_decision in override_choices else "NEEDS_REVIEW")
    )

    with st.form("manual_override_form"):
        col_ov1, col_ov2 = st.columns(2)
        with col_ov1:
            override_action = st.selectbox(
                "Select Override Decision",
                options=override_choices,
                index=override_choices.index(current_decision_choice),
                format_func=lambda s: f"{status_meta(s).symbol} {status_meta(s).label} ({s})",
            )
            reviewer_name = st.text_input("Reviewer Name / Employee ID", value="AP_ANALYST_01")
        with col_ov2:
            override_reason = st.text_area(
                "Mandatory Justification Reason",
                placeholder="Explain why this decision is being modified or verified...",
            )

        with st.expander("Optional reviewer corrections"):
            st.caption(
                "The original extraction payload remains preserved as source evidence. "
                "Corrected values are saved separately and shown in the audit trail."
            )
            vendors = session.execute(select(Vendor).order_by(Vendor.legal_name)).scalars().all()
            purchase_orders = (
                session.execute(select(PurchaseOrder).order_by(PurchaseOrder.po_number)).scalars().all()
            )
            selected_vendor_id = st.selectbox(
                "Correct vendor (optional)",
                options=[None] + [vendor.id for vendor in vendors],
                format_func=lambda value: "Keep current vendor"
                if value is None
                else next(
                    (f"{vendor.legal_name} (ID {vendor.id})" for vendor in vendors if vendor.id == value),
                    str(value),
                ),
            )
            selected_po_id = st.selectbox(
                "Correct purchase order (optional)",
                options=[None] + [po.id for po in purchase_orders],
                format_func=lambda value: "Keep current purchase order"
                if value is None
                else next(
                    (f"{po.po_number} — {po.vendor.legal_name}" for po in purchase_orders if po.id == value),
                    str(value),
                ),
            )
            corrected_invoice_number = st.text_input(
                "Correct invoice number (leave unchanged to keep current)",
                value=invoice.invoice_number or "",
            )
            currency_choices = ["Keep current", "Unconfirmed", *SUPPORTED_CURRENCIES]
            selected_currency = st.selectbox(
                "Confirm/correct currency (use the PO or vendor record as evidence)",
                options=currency_choices,
                format_func=lambda value: (
                    f"Keep current ({invoice.currency})"
                    if value == "Keep current" and invoice.currency
                    else "Keep current"
                    if value == "Keep current"
                    else value
                ),
            )
            corrected_total_amount = st.number_input(
                f"Correct total amount ({invoice.currency or 'currency unconfirmed'})",
                min_value=0.0,
                value=float(invoice.total_amount or 0.0),
                step=0.01,
                format="%.2f",
            )

        submit_override = st.form_submit_button("💾 Save Manual Override & Update Audit Trail", type="primary")

        if submit_override:
            if not override_reason.strip():
                st.error("A mandatory justification reason is required to submit a manual override.")
            else:
                try:
                    orchestrator = WorkflowOrchestrator(session)
                    corrected_fields: dict[str, object] = {}
                    if selected_vendor_id is not None:
                        corrected_fields["vendor_id"] = selected_vendor_id
                    if selected_po_id is not None:
                        corrected_fields["matched_po_id"] = selected_po_id
                    if (corrected_invoice_number.strip() or None) != invoice.invoice_number:
                        corrected_fields["invoice_number"] = corrected_invoice_number
                    if selected_currency != "Keep current":
                        corrected_fields["currency"] = (
                            None if selected_currency == "Unconfirmed" else selected_currency
                        )
                    current_total = (
                        round(float(invoice.total_amount), 2) if invoice.total_amount is not None else None
                    )
                    if round(float(corrected_total_amount), 2) != current_total:
                        corrected_fields["total_amount"] = corrected_total_amount
                    updated_invoice = orchestrator.apply_manual_override(
                        invoice_id=invoice.id,
                        new_decision=override_action,
                        reason=override_reason,
                        actor=reviewer_name,
                        corrected_fields=corrected_fields or None,
                    )
                    if getattr(updated_invoice, "manual_override_noop", False):
                        st.session_state["_review_notice"] = (
                            "info",
                            "This exact review action was already recorded; no duplicate audit event was created.",
                        )
                    else:
                        st.session_state["_review_notice"] = (
                            "success",
                            f"Review saved: decision set to {override_action}, with an audit entry.",
                        )
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to apply override: {e}")

    st.markdown("---")

    # Complete Audit Trail Table (bounded query)
    st.subheader("📜 Complete Audit Trail")
    logs = (
        session.execute(
            select(AuditLog)
            .where(AuditLog.invoice_id == invoice.id)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .limit(ui_config.MAX_AUDIT_ROWS)
        )
        .scalars()
        .all()
    )

    if logs:
        review_logs = [log for log in logs if log.action == "MANUAL_OVERRIDE"]
        if review_logs:
            st.markdown("#### Reviewer corrections — before and after")
            for log in review_logs:
                try:
                    payload = json.loads(log.details or "{}")
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                with st.expander(
                    f"{log.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC · {log.actor} · "
                    f"{log.previous_status or '—'} → {log.new_status or '—'}",
                    expanded=False,
                ):
                    st.write(f"**Reason:** {payload.get('reason') or log.details or '—'}")
                    changes = payload.get("changes", {})
                    if changes:
                        change_rows = [
                            {
                                "Field": key.replace("_", " ").title(),
                                "Before": values.get("before"),
                                "After": values.get("after"),
                            }
                            for key, values in changes.items()
                        ]
                        render_table(
                            change_rows,
                            ["Field", "Before", "After"],
                            caption="Field corrections in manual review",
                        )
                    else:
                        st.caption("No extracted fields changed in this review action.")

        audit_rows = []
        for log in logs:
            audit_rows.append(
                {
                    "Timestamp (UTC)": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "Action": log.action,
                    "Actor": log.actor,
                    "Previous Status": log.previous_status or "—",
                    "New Status": log.new_status or "—",
                    "Details": log.details or "—",
                }
            )
        render_table(
            audit_rows,
            ["Timestamp (UTC)", "Action", "Actor", "Previous Status", "New Status", "Details"],
            caption=f"Audit trail for invoice #{invoice.id}",
            wrap_columns=["Details"],
        )
    else:
        st.caption("No audit logs found for this invoice.")
