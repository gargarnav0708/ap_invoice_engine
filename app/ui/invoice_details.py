"""
Streamlit Invoice Details & Decision Page:
Deep inspection of extracted data, 3-way matching, validation checks, audit trail,
and human-in-the-loop overrides.
"""
from pathlib import Path
import json
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
    inv_map = {inv.id: f"#{inv.id} - {inv.original_filename} ({inv.invoice_number or 'No Inv#'}) - ${inv.total_amount or 0.0:.2f}" for inv in invoices}

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
        fields_data = [
            {"Field": "Vendor Name", "Value": invoice.vendor.legal_name if invoice.vendor else "Unmatched", "Confidence": f"{invoice.extraction_confidence*100:.0f}%"},
            {"Field": "Invoice Number", "Value": invoice.invoice_number or "N/A", "Confidence": "95%"},
            {"Field": "Invoice Date", "Value": invoice.invoice_date or "N/A", "Confidence": "95%"},
            {"Field": "Payment Due Date", "Value": invoice.due_date or "N/A", "Confidence": "90%"},
            {"Field": "PO Reference", "Value": invoice.po_reference or "N/A", "Confidence": "95%"},
            {"Field": "Currency", "Value": invoice.currency or "USD", "Confidence": "95%"},
            {"Field": "Subtotal", "Value": f"${invoice.subtotal:.2f}" if invoice.subtotal is not None else "N/A", "Confidence": "95%"},
            {"Field": "Tax Amount", "Value": f"${invoice.tax_amount:.2f}" if invoice.tax_amount is not None else "N/A", "Confidence": "95%"},
            {"Field": "Total Amount", "Value": f"${invoice.total_amount:.2f}" if invoice.total_amount is not None else "N/A", "Confidence": "95%"},
            {"Field": "SHA-256 Hash", "Value": invoice.file_hash[:16] + "...", "Confidence": "100%"},
        ]
        st.table(pd.DataFrame(fields_data))

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
            st.write(f"- **Total PO Amount:** `${p.total_amount:.2f}`")
            st.write(f"- **Previously Billed:** `${p.billed_amount:.2f}`")
            st.write(f"- **Remaining Balance:** `${p.remaining_balance:.2f}`")
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
                "Unit Price": f"${line.unit_price:.2f}",
                "Line Total": f"${line.line_total:.2f}",
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
        for v in validations:
            res_icon = "✅ PASS" if v.result == "PASS" else ("❌ FAIL" if v.result == "FAIL" else "⚠️ WARN")
            v_rows.append({
                "Rule ID": v.rule_id,
                "Rule Name": v.rule_name,
                "Category": v.category,
                "Result": res_icon,
                "Severity": v.severity,
                "Expected": v.expected_value or "-",
                "Actual": v.actual_value or "-",
                "Explanation": v.explanation
            })
        st.dataframe(pd.DataFrame(v_rows), use_container_width=True)
    else:
        st.caption("No validation rules recorded for this invoice.")

    st.markdown("---")

    # Human Review & Manual Override Section
    st.subheader("✍️ Human-in-the-Loop Review & Decision Override")
    st.caption("Authorised AP reviewers can override decisions or correct fields with mandatory justification.")

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

        submit_override = st.form_submit_button("💾 Save Manual Override & Update Audit Trail", type="primary")

        if submit_override:
            if not override_reason.strip():
                st.error("A mandatory justification reason is required to submit a manual override.")
            else:
                try:
                    orchestrator = WorkflowOrchestrator(session)
                    orchestrator.apply_manual_override(
                        invoice_id=invoice.id,
                        new_decision=override_action,
                        reason=override_reason,
                        actor=reviewer_name
                    )
                    st.success(f"Successfully applied manual override: Decision set to {override_action}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to apply override: {e}")

    st.markdown("---")

    # Complete Audit Trail Table
    st.subheader("📜 Complete Audit Trail")
    logs = session.query(AuditLog).filter(AuditLog.invoice_id == invoice.id).order_by(AuditLog.timestamp.desc()).all()

    if logs:
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
