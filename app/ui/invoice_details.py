"""
Streamlit Invoice Details & Decision Page:
Deep inspection of extracted data, 3-way matching, validation checks, audit trail,
and human-in-the-loop overrides.
"""
from pathlib import Path
import json
from typing import Any
import streamlit as st
import pandas as pd
from sqlalchemy.orm import Session
import pymupdf
from PIL import Image
import io

from app.database.entities import Invoice, ValidationResultEntity, AuditLog, Vendor, PurchaseOrder
from app.core.schemas import DecisionStatus
from app.workflow.orchestrator import WorkflowOrchestrator


def render_invoice_details(session: Session):
    st.markdown("## 🔍 Invoice Audit & Decision Workbench")

    invoices = session.query(Invoice).order_by(Invoice.created_at.desc()).all()
    if not invoices:
        st.info("No invoices processed yet. Upload an invoice to inspect.")
        return

    # Select invoice from dropdown
    default_id = st.session_state.get("selected_invoice_id", invoices[0].id)
    inv_map = {
        inv.id: (
            f"#{inv.id} - {inv.original_filename} ({inv.invoice_number or 'No Inv#'}) - "
            f"{inv.currency + ' ' if inv.currency else 'Currency unconfirmed '}{inv.total_amount or 0.0:.2f}"
        )
        for inv in invoices
    }

    selected_id = st.selectbox(
        "Select Invoice to Audit",
        options=list(inv_map.keys()),
        format_func=lambda x: inv_map[x],
        index=list(inv_map.keys()).index(default_id) if default_id in inv_map else 0
    )

    invoice = session.query(Invoice).filter(Invoice.id == selected_id).first()
    if not invoice:
        st.error("Invoice record not found.")
        return

    # Header section with Decision Pill
    effective_status = invoice.manual_override_decision or invoice.final_decision
    badge_color = {
        "APPROVED": "green",
        "NEEDS_REVIEW": "orange",
        "BLOCKED": "red"
    }.get(effective_status, "blue")

    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        st.markdown(f"### Invoice: `{invoice.invoice_number or 'UNASSIGNED'}` | File: `{invoice.original_filename}`")
        if invoice.manual_override_decision:
            st.warning(
                f"⚠️ **Manual Override Active:** Automated decision was `{invoice.final_decision}`, "
                f"overridden to `{invoice.manual_override_decision}` by `{invoice.manual_override_by}`."
            )
    with col_h2:
        st.markdown(f"### Status: :{badge_color}[{effective_status}]")

    st.markdown("---")

    # Document Preview + Extracted Data Layout
    col_preview, col_fields = st.columns([1, 1])

    with col_preview:
        st.subheader("📄 Document Preview")
        file_path = Path(invoice.file_path)
        if file_path.exists():
            try:
                doc = pymupdf.open(str(file_path))
                if len(doc) > 0:
                    page = doc[0]
                    pix = page.get_pixmap(dpi=130)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    st.image(img, caption=f"Page 1 of {len(doc)} ({file_path.name})", use_container_width=True)
                doc.close()
            except Exception as e:
                st.caption(f"Preview unavailable: {e}")
        else:
            st.caption("Document file not found on disk.")

    with col_fields:
        st.subheader("Extracted Structured Fields")

        # Parse the persisted extraction payload for real per-field confidence
        # and the verbatim evidence snippet each value was taken from.
        extracted: dict[str, Any] = {}
        try:
            extracted = json.loads(invoice.extracted_data) if invoice.extracted_data else {}
        except Exception:
            extracted = {}

        def fld(key: str):
            """Returns (value, confidence_pct_str, evidence) for an extracted field."""
            obj = extracted.get(key) if isinstance(extracted, dict) else None
            if isinstance(obj, dict):
                conf = obj.get("confidence")
                conf_str = f"{conf*100:.0f}%" if isinstance(conf, (int, float)) else "—"
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
                    currency_code = currency_value if currency_value in {
                        "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "HKD", "JPY", "CNY", "INR"
                    } else None
                    shown = f"{currency_code + ' ' if currency_code else ''}{float(value):,.2f}"
                    if not currency_code:
                        shown += " (currency unconfirmed)"
                except (TypeError, ValueError):
                    shown = str(value)
            else:
                shown = str(value)
            ev_short = ev if len(str(ev)) <= 48 else str(ev)[:45] + "…"
            fields_data.append(
                {
                    "Field": label,
                    "Value": shown,
                    "Confidence": conf_str,
                    "OCR Conf.": ocr_confidence_str,
                    "Evidence": ev_short,
                }
            )

        fields_data.append(
            {"Field": "SHA-256 Hash", "Value": invoice.file_hash[:16] + "…",
             "Confidence": "100%", "Evidence": "binary file hash"}
        )

        st.dataframe(
            pd.DataFrame(fields_data),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Confidence": st.column_config.TextColumn(
                    "Conf.", help="Per-field extraction confidence (lower = more likely to need review)"
                ),
                "OCR Conf.": st.column_config.TextColumn(
                    "OCR Conf.", help="Tesseract word confidence for the exact evidence snippet"
                ),
                "Evidence": st.column_config.TextColumn(
                    "Evidence", help="Verbatim snippet the value was extracted from"
                ),
            },
        )
        if extracted.get("overall_confidence") is not None:
            st.caption(f"Overall extraction confidence: **{extracted['overall_confidence']*100:.0f}%**")
        if extracted.get("ocr_confidence") is not None:
            st.caption(f"OCR word confidence: **{extracted['ocr_confidence']:.1f}%**")
        if extracted.get("raw_text"):
            with st.expander("OCR / source text sent to the extractor"):
                st.text(extracted["raw_text"])

    if invoice.manual_override_at:
        st.markdown("#### Reviewer-accepted values now in effect")
        final_vendor = invoice.vendor.legal_name if invoice.vendor else "Unmatched"
        final_po = invoice.matched_po.po_number if invoice.matched_po else "Unlinked"
        value_cols = st.columns(5)
        value_cols[0].metric("Vendor", final_vendor)
        value_cols[1].metric("Invoice #", invoice.invoice_number or "—")
        value_cols[2].metric("Purchase order", final_po)
        value_cols[3].metric("Currency", invoice.currency or "Unconfirmed")
        value_cols[4].metric("Total", f"{invoice.currency or 'Currency unconfirmed'} {invoice.total_amount:,.2f}" if invoice.total_amount is not None else "—")

    st.markdown("---")

    # 3-Way Match: Vendor, PO, Line Items
    st.subheader("3-Way Procurement Comparison")
    col_v, col_p = st.columns(2)

    with col_v:
        st.markdown("#### Matched Vendor")
        if invoice.vendor:
            v = invoice.vendor
            st.success(f"**{v.legal_name}**")
            st.write(f"- **Tax ID:** `{v.tax_id}`")
            st.write(f"- **Address:** {v.address}")
            st.write(f"- **Approval Status:** {'✅ Approved' if v.is_approved else '❌ Unapproved'}")
        else:
            st.error("No registered vendor matched.")

    with col_p:
        st.markdown("#### Matched Purchase Order")
        if invoice.matched_po:
            p = invoice.matched_po
            st.success(f"**PO: {p.po_number}**")
            st.write(f"- **Total PO Amount:** `{p.currency} {p.total_amount:.2f}`")
            st.write(f"- **Previously Billed:** `{p.currency} {p.billed_amount:.2f}`")
            st.write(f"- **Remaining Balance:** `{p.currency} {p.remaining_balance:.2f}`")
            st.write(f"- **PO Status:** `{p.status}`")
        else:
            st.warning("No Purchase Order linked to this invoice.")

    # Line Item Table
    st.markdown("#### Line Items & PO Comparison")
    if invoice.line_items:
        items_rows = []
        for line in invoice.line_items:
            po_match_desc = line.matched_po_line.description if line.matched_po_line else "Unmatched"
            items_rows.append({
                "Line #": line.line_number,
                "Description": line.description,
                "Qty": line.quantity,
                "Unit Price": f"{invoice.currency + ' ' if invoice.currency else ''}{line.unit_price:.2f}",
                "Line Total": f"{invoice.currency + ' ' if invoice.currency else ''}{line.line_total:.2f}",
                "Matched PO Line": po_match_desc,
            })
        st.dataframe(pd.DataFrame(items_rows), use_container_width=True)
    else:
        st.caption("No individual line items recorded.")

    st.markdown("---")

    # Deterministic Validation Rules Results Table
    st.subheader("Deterministic Validation Rules Results")
    validations = session.query(ValidationResultEntity).filter(
        ValidationResultEntity.invoice_id == invoice.id
    ).all()

    if validations:
        v_rows = []
        for validation in validations:
            res_icon = "✅ PASS" if validation.result == "PASS" else ("❌ FAIL" if validation.result == "FAIL" else "⚠️ WARN")
            v_rows.append({
                "Rule ID": validation.rule_id,
                "Rule Name": validation.rule_name,
                "Category": validation.category,
                "Result": res_icon,
                "Severity": validation.severity,
                "Expected": validation.expected_value or "-",
                "Actual": validation.actual_value or "-",
                "Explanation": validation.explanation
            })
        st.dataframe(pd.DataFrame(v_rows), use_container_width=True)
    else:
        st.caption("No validation rules recorded for this invoice.")

    st.markdown("---")

    # Human Review & Manual Override Section
    st.subheader("✍️ Human-in-the-Loop Review & Decision Override")
    st.caption("Demo reviewer identity is entered below. Decision changes and corrections require a reason and are recorded with before/after values.")

    review_notice = st.session_state.pop("_review_notice", None)
    if review_notice:
        kind, message = review_notice
        getattr(st, kind)(message)

    with st.form("manual_override_form"):
        col_ov1, col_ov2 = st.columns(2)
        with col_ov1:
            override_action = st.selectbox(
                "Select Override Decision",
                options=["APPROVED", "NEEDS_REVIEW", "BLOCKED"],
                index=["APPROVED", "NEEDS_REVIEW", "BLOCKED"].index(effective_status) if effective_status in ["APPROVED", "NEEDS_REVIEW", "BLOCKED"] else 0
            )
            reviewer_name = st.text_input("Reviewer Name / Employee ID", value="AP_ANALYST_01")
        with col_ov2:
            override_reason = st.text_area(
                "Mandatory Justification Reason",
                placeholder="Explain why this decision is being modified or verified..."
            )

        with st.expander("Optional reviewer corrections"):
            st.caption("The original extraction payload remains preserved as source evidence. Corrected values are saved separately and shown in the audit trail.")
            vendors = session.query(Vendor).order_by(Vendor.legal_name).all()
            purchase_orders = session.query(PurchaseOrder).order_by(PurchaseOrder.po_number).all()
            selected_vendor_id = st.selectbox(
                "Correct vendor (optional)",
                options=[None] + [vendor.id for vendor in vendors],
                format_func=lambda value: "Keep current vendor" if value is None else next(
                    (f"{vendor.legal_name} (ID {vendor.id})" for vendor in vendors if vendor.id == value), str(value)
                ),
            )
            selected_po_id = st.selectbox(
                "Correct purchase order (optional)",
                options=[None] + [po.id for po in purchase_orders],
                format_func=lambda value: "Keep current purchase order" if value is None else next(
                    (f"{po.po_number} — {po.vendor.legal_name}" for po in purchase_orders if po.id == value), str(value)
                ),
            )
            corrected_invoice_number = st.text_input(
                "Correct invoice number (leave unchanged to keep current)",
                value=invoice.invoice_number or "",
            )
            currency_choices = ["Keep current", "Unconfirmed", "USD", "HKD", "CAD", "AUD", "NZD", "EUR", "GBP", "INR", "JPY", "CNY"]
            selected_currency = st.selectbox(
                "Confirm/correct currency (use the PO or vendor record as evidence)",
                options=currency_choices,
                format_func=lambda value: (
                    f"Keep current ({invoice.currency})" if value == "Keep current" and invoice.currency
                    else "Keep current" if value == "Keep current"
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
                        corrected_fields["currency"] = None if selected_currency == "Unconfirmed" else selected_currency
                    current_total = round(float(invoice.total_amount), 2) if invoice.total_amount is not None else None
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
                            "info", "This exact review action was already recorded; no duplicate audit event was created."
                        )
                    else:
                        st.session_state["_review_notice"] = (
                            "success", f"Review saved: decision set to {override_action}, with an audit entry."
                        )
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to apply override: {e}")

    st.markdown("---")

    # Complete Audit Trail Table
    st.subheader("📜 Complete Audit Trail")
    logs = session.query(AuditLog).filter(AuditLog.invoice_id == invoice.id).order_by(AuditLog.timestamp.desc()).all()

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
                            {"Field": key.replace("_", " ").title(), "Before": values.get("before"), "After": values.get("after")}
                            for key, values in changes.items()
                        ]
                        st.dataframe(pd.DataFrame(change_rows), hide_index=True, use_container_width=True)
                    else:
                        st.caption("No extracted fields changed in this review action.")

        audit_rows = []
        for log in logs:
            audit_rows.append({
                "Timestamp (UTC)": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "Action": log.action,
                "Actor": log.actor,
                "Previous Status": log.previous_status or "-",
                "New Status": log.new_status or "-",
                "Details": log.details or "-"
            })
        st.dataframe(pd.DataFrame(audit_rows), use_container_width=True)
    else:
        st.caption("No audit logs found for this invoice.")
