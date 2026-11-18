"""
Streamlit Live Processing View: Transparent stage-by-stage workflow execution inspector.
"""
import json
import streamlit as st
from sqlalchemy.orm import Session

from app.database.entities import ProcessingRun, Invoice


def render_processing_view(session: Session):
    st.markdown("## ⚙️ Live Processing Pipeline Inspector")
    st.caption("Inspect stage-by-stage execution trace, durations, extracted data, and matching logs.")

    # Select which run to inspect
    runs = session.query(ProcessingRun).order_by(ProcessingRun.start_time.desc()).limit(20).all()

    if not runs:
        st.info("No processing runs recorded yet. Process an invoice first.")
        return

    # Choose run from dropdown or default to latest
    selected_run_id = st.session_state.get("latest_run_id", runs[0].id)
    run_options = {r.id: f"Run #{r.id} - Invoice #{r.invoice_id} ({r.final_decision or r.execution_status}) - {r.start_time.strftime('%H:%M:%S')}" for r in runs}

    chosen_run_id = st.selectbox(
        "Select Processing Run",
        options=list(run_options.keys()),
        format_func=lambda x: run_options[x],
        index=list(run_options.keys()).index(selected_run_id) if selected_run_id in run_options else 0
    )

    run = session.query(ProcessingRun).filter(ProcessingRun.id == chosen_run_id).first()
    if not run:
        st.error("Run not found.")
        return

    # Run Header Summary
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Invoice ID", f"#{run.invoice_id}")
    with col2:
        st.metric("Status", run.execution_status)
    with col3:
        st.metric("Total Duration", f"{run.duration_ms:.1f} ms" if run.duration_ms else "N/A")
    with col4:
        decision_badge = {
            "APPROVED": "🟢 APPROVED",
            "NEEDS_REVIEW": "🟠 NEEDS REVIEW",
            "BLOCKED": "🔴 BLOCKED"
        }.get(
            run.final_decision or "",
            "🔴 FAILED · NEEDS REVIEW" if run.execution_status == "FAILED"
            else run.final_decision or "IN PROGRESS",
        )
        st.metric("Final Outcome", decision_badge)

    if run.execution_status == "FAILED":
        st.error(f"This run failed and needs review. {run.error_message or 'See the failed stage below for details.'}")

    st.markdown("---")
    st.subheader("19-Stage Workflow Execution Trace")

    if run.stages_log:
        try:
            stages_data = json.loads(run.stages_log)
        except Exception:
            stages_data = []

        status_icons = {
            "COMPLETED": "✅",
            "RUNNING": "⏳",
            "FAILED": "❌",
            "SKIPPED": "⚠️",
            "PENDING": "⚪"
        }

        for idx, stage in enumerate(stages_data):
            icon = status_icons.get(stage.get("status"), "🔹")
            stage_name = stage.get("stage_name", f"Stage {idx+1}")
            dur = stage.get("duration_ms", 0.0)
            details = stage.get("details", "")
            err = stage.get("error", "")

            with st.expander(f"{icon} **{stage_name}** ({dur:.1f} ms)", expanded=(stage.get("status") in ("FAILED", "RUNNING"))):
                st.write(f"**Status:** `{stage.get('status')}`")
                st.write(f"**Started At:** `{stage.get('start_time')}`")
                st.write(f"**Completed At:** `{stage.get('completion_time')}`")
                if details:
                    st.info(f"**Details:** {details}")
                if err:
                    st.error(f"**Error:** {err}")
    else:
        st.caption("No stage trace logs available for this run.")
