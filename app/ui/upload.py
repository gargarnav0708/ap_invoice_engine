"""
Streamlit Invoice Upload View: Drag-and-drop file upload and 1-click test scenario runners.
"""
from pathlib import Path
import streamlit as st
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.workflow.orchestrator import WorkflowOrchestrator


def render_upload(session: Session):
    st.markdown("## 📥 Ingest & Process Invoices")
    st.caption("Upload vendor PDF invoices for AI extraction, 3-way PO matching, and deterministic validation.")

    orchestrator = WorkflowOrchestrator(session)

    # Tabs for Upload vs Pre-built Synthetic Scenarios
    tab_upload, tab_scenarios = st.tabs(["Upload Document", "Quick Test Scenarios (1-Click)"])

    with tab_upload:
        st.subheader("Upload Invoice PDF")
        uploaded_file = st.file_uploader("Choose a PDF invoice", type=["pdf"])

        if uploaded_file is not None:
            col_info1, col_info2 = st.columns(2)
            with col_info1:
                st.write(f"**Filename:** `{uploaded_file.name}`")
            with col_info2:
                st.write(f"**File Size:** `{len(uploaded_file.getvalue()) / 1024:.1f} KB`")

            if st.button("🚀 Process Invoice Now", type="primary", use_container_width=True):
                with st.spinner("Processing invoice through 19-stage pipeline..."):
                    try:
                        file_bytes = uploaded_file.getvalue()
                        inv_entity, run_entity = orchestrator.process_invoice(
                            file_bytes=file_bytes,
                            original_filename=uploaded_file.name
                        )
                        st.session_state["selected_invoice_id"] = inv_entity.id
                        st.session_state["latest_run_id"] = run_entity.id
                        st.success(f"Processing Complete! Decision: **{inv_entity.final_decision}**")
                        st.info("Navigate to 'Live Processing View' or 'Invoice Details' to inspect the audit trail.")
                    except Exception as e:
                        st.error(f"Processing failed: {e}")

    with tab_scenarios:
        st.subheader("Run Verified Case Study Scenarios")
        st.write("Demonstrate the core AP engine capabilities on synthetic benchmark fixtures:")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### Scenario 1: Clean Invoice")
            st.caption("Approved vendor ('Acme Industrial Supplies'), valid PO ('PO-2026-001'), matching line items and math.")
            st.caption("🎯 **Expected Outcome:** `APPROVED`")
            if st.button("▶ Run Scenario 1", key="btn_s1", use_container_width=True):
                s1_file = settings.SAMPLE_DATA_DIR / "scenario_1_clean_invoice.pdf"
                _run_file_scenario(orchestrator, s1_file, "Scenario 1: Clean Invoice")

            st.markdown("---")

            st.markdown("### Scenario 3: Split PO Overbilling")
            st.caption("PO-2026-002 has only $500 remaining balance. Invoice bills $1,100, exceeding available balance.")
            st.caption("🎯 **Expected Outcome:** `BLOCKED` (PO Balance Exceeded)")
            if st.button("▶ Run Scenario 3", key="btn_s3", use_container_width=True):
                s3_file = settings.SAMPLE_DATA_DIR / "scenario_3_split_overbilling.pdf"
                _run_file_scenario(orchestrator, s3_file, "Scenario 3: Split Overbilling")

        with col2:
            st.markdown("### Scenario 2: Duplicate Invoice")
            st.caption("Re-submission of the identical clean invoice file / invoice number.")
            st.caption("🎯 **Expected Outcome:** `BLOCKED` (Confirmed Duplicate)")
            if st.button("▶ Run Scenario 2", key="btn_s2", use_container_width=True):
                s2_file = settings.SAMPLE_DATA_DIR / "scenario_2_duplicate_invoice.pdf"
                _run_file_scenario(orchestrator, s2_file, "Scenario 2: Duplicate Invoice")

            st.markdown("---")

            st.markdown("### Scenario 4: Incomplete / Unreadable Invoice")
            st.caption("Document missing invoice number, PO reference, and clear tax arithmetic.")
            st.caption("🎯 **Expected Outcome:** `NEEDS_REVIEW` (Missing Critical Fields)")
            if st.button("▶ Run Scenario 4", key="btn_s4", use_container_width=True):
                s4_file = settings.SAMPLE_DATA_DIR / "scenario_4_incomplete_invoice.pdf"
                _run_file_scenario(orchestrator, s4_file, "Scenario 4: Incomplete Invoice")


def _run_file_scenario(orchestrator: WorkflowOrchestrator, file_path: Path, title: str):
    if not file_path.exists():
        st.error(f"Sample fixture not found at {file_path}. Run generate_test_invoices.py first.")
        return

    with st.spinner(f"Executing {title}..."):
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            inv_entity, run_entity = orchestrator.process_invoice(
                file_bytes=data,
                original_filename=file_path.name
            )
            st.session_state["selected_invoice_id"] = inv_entity.id
            st.session_state["latest_run_id"] = run_entity.id

            decision_color = {
                "APPROVED": "green",
                "NEEDS_REVIEW": "orange",
                "BLOCKED": "red"
            }.get(inv_entity.final_decision, "blue")

            st.markdown(f"#### Result: :{decision_color}[{inv_entity.final_decision}]")
            st.success(f"Processing completed for {file_path.name} (Invoice ID: {inv_entity.id})")
        except Exception as e:
            st.error(f"Execution failed: {e}")
