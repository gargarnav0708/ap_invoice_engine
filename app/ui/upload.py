"""
Streamlit Invoice Upload View: Drag-and-drop file upload and 1-click test scenario
runners, with a true live run view (each of the 19 stages ticking over as it
executes) and a final decision card showing the reasoned outcome.
"""
from pathlib import Path
import streamlit as st
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.workflow.orchestrator import WorkflowOrchestrator
from app.core.schemas import DecisionStatus


# Canonical ordered list of the 19 pipeline stages, shown greyed-out before they run.
PIPELINE_STAGES = [
    "1. Receive invoice",
    "2. Validate uploaded file",
    "3. Calculate file hash",
    "4. Check for exact duplicate",
    "5. Extract PDF text",
    "6. Run OCR when necessary",
    "7. Extract structured invoice information",
    "8. Validate extracted schema",
    "9. Normalise extracted fields",
    "10. Match vendor",
    "11. Match purchase order",
    "12. Detect duplicate invoices",
    "13. Match line items",
    "14. Run financial validation",
    "15. Run PO balance validation",
    "16. Generate final decision",
    "17. Save all results",
    "18. Update invoice and PO records",
    "19. Generate the final audit report",
]

_STATUS_ICON = {
    "PENDING": "⚪",
    "RUNNING": "⏳",
    "COMPLETED": "✅",
    "FAILED": "❌",
}


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
                _run_with_live_view(
                    orchestrator,
                    file_bytes=uploaded_file.getvalue(),
                    filename=uploaded_file.name,
                    title=uploaded_file.name,
                )

    with tab_scenarios:
        st.subheader("Run Verified Case Study Scenarios")
        st.write("Demonstrate the core AP engine capabilities on synthetic benchmark fixtures:")

        scenarios = [
            ("Scenario 1: Clean Invoice",
             "Approved vendor ('Acme Industrial Supplies'), valid PO ('PO-2026-001'), matching line items and math.",
             "APPROVED", "scenario_1_clean_invoice.pdf", "btn_s1"),
            ("Scenario 2: Duplicate Invoice",
             "Re-submission of the identical clean invoice file / invoice number.",
             "BLOCKED (Confirmed Duplicate)", "scenario_2_duplicate_invoice.pdf", "btn_s2"),
            ("Scenario 3: Split PO Overbilling",
             "PO-2026-002 has only $500 remaining balance. Invoice bills $1,100, exceeding available balance.",
             "BLOCKED (PO Balance Exceeded)", "scenario_3_split_overbilling.pdf", "btn_s3"),
            ("Scenario 4: Incomplete / Unreadable Invoice",
             "Document missing invoice number, PO reference, and clear tax arithmetic.",
             "NEEDS_REVIEW (Missing Critical Fields)", "scenario_4_incomplete_invoice.pdf", "btn_s4"),
        ]

        col1, col2 = st.columns(2)
        for idx, (name, desc, expected, filename, key) in enumerate(scenarios):
            target = col1 if idx % 2 == 0 else col2
            with target:
                st.markdown(f"### {name}")
                st.caption(desc)
                st.caption(f"🎯 **Expected Outcome:** `{expected}`")
                if st.button(f"▶ Run {name.split(':')[0]}", key=key, use_container_width=True):
                    st.session_state["_pending_scenario"] = (filename, name)
                st.markdown("---")

    # Run a queued scenario below the grid so the live view spans full width.
    pending = st.session_state.pop("_pending_scenario", None)
    if pending:
        filename, name = pending
        file_path = settings.SAMPLE_DATA_DIR / filename
        if not file_path.exists():
            st.error(f"Sample fixture not found at {file_path}. Run generate_test_invoices.py first.")
        else:
            _run_with_live_view(
                orchestrator,
                file_bytes=file_path.read_bytes(),
                filename=filename,
                title=name,
            )


def _run_with_live_view(orchestrator: WorkflowOrchestrator, file_bytes: bytes, filename: str, title: str):
    """Executes the pipeline while streaming each stage into a live view, then
    renders the final reasoned decision card."""
    st.markdown(f"### ⚙️ Live Run — {title}")
    stage_placeholder = st.empty()

    # Stage state and ordering (local copies so runs never mutate module globals).
    stages_order = list(PIPELINE_STAGES)
    stage_state = {name: {"status": "PENDING", "details": None, "duration_ms": None}
                   for name in stages_order}

    def render():
        lines = []
        for name in stages_order:
            s = stage_state[name]
            icon = _STATUS_ICON.get(s["status"], "⚪")
            line = f"{icon} **{name}**"
            if s["status"] == "COMPLETED" and s["duration_ms"] is not None:
                line += f"  ·  _{s['duration_ms']:.0f} ms_"
            lines.append(line)
            if s["details"] and s["status"] in ("COMPLETED", "FAILED"):
                lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ {s['details']}")
        stage_placeholder.markdown("\n\n".join(lines))

    def on_event(event: dict):
        name = event.get("stage_name")
        if name not in stage_state:
            # Unknown label — append it so nothing is silently dropped.
            stage_state[name] = {"status": "PENDING", "details": None, "duration_ms": None}
            stages_order.append(name)
        if event["type"] == "start":
            stage_state[name]["status"] = "RUNNING"
        elif event["type"] == "complete":
            stage_state[name]["status"] = "COMPLETED"
            stage_state[name]["details"] = event.get("details")
            stage_state[name]["duration_ms"] = event.get("duration_ms")
        elif event["type"] == "fail":
            stage_state[name]["status"] = "FAILED"
            stage_state[name]["details"] = event.get("error")
            stage_state[name]["duration_ms"] = event.get("duration_ms")
        render()

    render()  # initial all-pending render
    try:
        inv_entity, run_entity = orchestrator.process_invoice(
            file_bytes=file_bytes,
            original_filename=filename,
            progress_callback=on_event,
        )
    except Exception as e:
        st.error(f"Processing failed: {e}")
        return

    st.session_state["selected_invoice_id"] = inv_entity.id
    st.session_state["latest_run_id"] = run_entity.id

    _render_decision_card(inv_entity, run_entity)


def _render_decision_card(inv_entity, run_entity):
    """Renders the final reasoned decision with reasons and the recommended next action."""
    decision = getattr(run_entity, "decision_result", None)
    status = inv_entity.final_decision

    st.markdown("### Decision")
    palette = {
        "APPROVED": ("green", "🟢"),
        "NEEDS_REVIEW": ("orange", "🟠"),
        "BLOCKED": ("red", "🔴"),
    }
    color, emoji = palette.get(status, ("blue", "🔵"))

    st.markdown(f"## {emoji} :{color}[{status}]")
    if decision is not None:
        st.write(decision.summary)

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Decision", status)
    col_b.metric("Extraction Confidence", f"{(inv_entity.extraction_confidence or 0)*100:.0f}%")
    col_c.metric("Processing Time", f"{run_entity.duration_ms:.0f} ms" if run_entity.duration_ms else "—")

    if decision is not None:
        if decision.checks_failed:
            st.markdown("#### Why")
            for item in decision.checks_failed:
                st.markdown(f"- ❌ {item}")
        if decision.warnings:
            st.markdown("#### Flags")
            for item in decision.warnings:
                st.markdown(f"- ⚠️ {item}")
        st.info(f"**Recommended action:** {decision.recommended_action}")
        with st.expander(f"All checks ({len(decision.checks_passed)} passed)", expanded=False):
            for item in decision.checks_passed:
                st.markdown(f"- ✅ {item}")

    st.success(
        f"Invoice #{inv_entity.id} processed. "
        "Open **Invoice Details** for the full audit workbench, or **Live Processing View** for the saved stage trace."
    )
