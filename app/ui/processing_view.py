"""
Streamlit Live Processing View: transparent stage-by-stage workflow execution inspector.

* Runs selector is bounded (no unbounded ``.all()``) and fetches only lightweight summary columns.
* The selected run is fetched directly by primary key.
* When the selected run is ``RUNNING`` and not stale, a ``st.fragment(run_every=...)`` panel
  refreshes the run status and persisted ``stages_log`` automatically without blocking the app
  or resetting the user's selected run. Polling stops automatically on ``SUCCESS`` or ``FAILED``.
"""
from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from app.config import ui as ui_config
from app.database.db import get_db
from app.database.entities import ProcessingRun
from app.database.queries import get_run, list_recent_runs
from app.ui.components import render_kpis, render_status_banner, render_table
from app.ui.presentation import Kpi, run_display_status, stage_badge_html, status_meta
from app.ui.run_trace import (
    build_run_trace,
    format_duration,
    is_stale,
    should_poll,
)
from app.utils.time import now_utc
from app.workflow.stages import PIPELINE_STAGES


def _render_run_details(run: ProcessingRun) -> None:
    now = now_utc()
    stale = run.execution_status == "RUNNING" and is_stale(
        run.start_time, now, ui_config.STALE_RUN_MINUTES
    )
    display_status = run_display_status(run.execution_status, run.final_decision, is_stale=stale)
    meta = status_meta(display_status)

    trace = build_run_trace(run.stages_log, run.execution_status)
    total_stages = len(PIPELINE_STAGES)

    render_kpis(
        [
            Kpi("Run ID", f"#{run.id}", f"Invoice #{run.invoice_id}", "info"),
            Kpi("Execution status", run.execution_status, f"Stage: {run.current_stage}"),
            Kpi(
                "Stages completed",
                f"{trace.completed} / {total_stages}",
                "Persisted trace",
                "success" if trace.completed == total_stages else "info",
            ),
            Kpi("Total duration", format_duration(run.duration_ms), "Wall-clock time"),
            Kpi("Final outcome", f"{meta.symbol} {meta.label}", meta.description, meta.tone),
        ],
        label=f"Processing run #{run.id} summary",
    )

    if run.execution_status == "FAILED":
        failed_stage = trace.failed_stage
        stage_hint = (
            f" at stage {failed_stage.number}. {failed_stage.name}"
            if failed_stage and failed_stage.number
            else (f" at {run.current_stage}" if run.current_stage else "")
        )
        render_status_banner(
            "FAILED",
            title=f"Run #{run.id} failed{stage_hint} — routed to human review",
            body=run.error_message
            or "An unexpected error stopped the pipeline. Inspect the failed stage below and open Invoice Details to review.",
        )
    elif stale:
        render_status_banner(
            "STALLED",
            title=f"Run #{run.id} has not updated for over {ui_config.STALE_RUN_MINUTES} minutes",
            body="Live polling has paused because this run appears stalled. Re-upload the document or inspect Invoice Details.",
        )
    elif run.execution_status == "RUNNING":
        render_status_banner(
            "IN_PROGRESS",
            title=f"Run #{run.id} is active — auto-refreshing every {ui_config.LIVE_REFRESH_SECONDS:.0f}s",
            body=f"Current stage: {run.current_stage}. This panel will stop polling automatically once the run finishes.",
        )
    else:
        render_status_banner(
            display_status,
            title=f"Run #{run.id} completed with outcome: {meta.label}",
            body=meta.description,
        )

    if trace.trace_error:
        st.warning(trace.trace_error)

    st.markdown("---")
    st.subheader("19-Stage Workflow Execution Trace")

    if not trace.stages:
        st.caption("No stage trace logs recorded for this run yet.")
        return

    summary_rows = []
    for stage in trace.stages:
        num_label = f"{stage.number:02d}" if stage.number is not None else "—"
        detail_Or_err = stage.error or stage.details or "—"
        summary_rows.append(
            {
                "#": num_label,
                "Stage": stage.name,
                "Status": stage.status,
                "Duration": format_duration(stage.duration_ms),
                "Details / Evidence": detail_Or_err,
            }
        )

    render_table(
        summary_rows,
        ["#", "Stage", "Status", "Duration", "Details / Evidence"],
        caption=f"19-stage execution trace for run #{run.id}",
        column_kinds={"Status": "stage_status"},
        wrap_columns=["Details / Evidence", "Stage"],
        numeric_columns=["#", "Duration"],
    )

    with st.expander("Inspect individual stage timestamps & full evidence", expanded=(run.execution_status == "FAILED")):
        for idx, stage in enumerate(trace.stages, start=1):
            num = stage.number if stage.number is not None else idx
            dur = format_duration(stage.duration_ms)
            header = f"{num}. {stage.name} — {stage.status} ({dur})"
            with st.expander(header, expanded=(stage.status in {"FAILED", "RUNNING"})):
                st.html(
                    f'<div class="ap-stage">{stage_badge_html(stage.status)}'
                    f'<span><strong>Started:</strong> {stage.start_time or "—"} · '
                    f'<strong>Completed:</strong> {stage.completion_time or "—"}</span></div>'
                )
                if stage.details:
                    st.info(f"**Details:** {stage.details}")
                if stage.error:
                    st.error(f"**Actionable Error:** {stage.error}")


@st.fragment(run_every=ui_config.LIVE_REFRESH_SECONDS)
def _live_run_fragment(run_id: int) -> None:
    """Polls a RUNNING run at a modest interval using a fresh DB session."""
    with get_db() as poll_session:
        run = get_run(poll_session, run_id)
        if run is None:
            st.error(f"Run #{run_id} not found.")
            return
        _render_run_details(run)
        if not should_poll(run.execution_status, run.start_time, now_utc(), ui_config.STALE_RUN_MINUTES):
            # Trigger a single full-page rerun when transitioning to a terminal state
            # so the selector label in the parent view reflects the final outcome.
            if st.session_state.get("_live_watching_run_id") == run_id:
                st.session_state.pop("_live_watching_run_id", None)
                st.rerun()


def render_processing_view(session: Session):
    st.markdown("## ⚙️ Live Processing Pipeline Inspector")
    st.caption(
        f"Inspect stage-by-stage execution trace, durations, and matching evidence "
        f"(showing up to {ui_config.MAX_RUN_OPTIONS} most recent runs)."
    )

    preferred_run_id = st.session_state.get("selected_run_id") or st.session_state.get("latest_run_id")
    runs = list_recent_runs(session, limit=ui_config.MAX_RUN_OPTIONS, include_run_id=preferred_run_id)

    if not runs:
        st.info("No processing runs recorded yet. Process an invoice first.")
        return

    run_options: dict[int, str] = {}
    for r in runs:
        disp = run_display_status(r.execution_status, r.final_decision)
        meta = status_meta(disp)
        ts_str = r.start_time.strftime("%Y-%m-%d %H:%M:%S") if hasattr(r.start_time, "strftime") else str(r.start_time)
        run_options[r.id] = f"Run #{r.id} · Invoice #{r.invoice_id} · {meta.symbol} {meta.label} · {ts_str}"

    option_ids = list(run_options.keys())
    default_index = option_ids.index(preferred_run_id) if preferred_run_id in run_options else 0

    col_sel, col_btn = st.columns([3, 1], vertical_alignment="bottom")
    with col_sel:
        chosen_run_id = st.selectbox(
            "Select Processing Run",
            options=option_ids,
            index=default_index,
            format_func=lambda x: run_options.get(x, f"Run #{x}"),
        )
    st.session_state["selected_run_id"] = chosen_run_id
    st.session_state["latest_run_id"] = chosen_run_id

    run = get_run(session, chosen_run_id)
    if not run:
        st.error("Run not found.")
        return

    with col_btn:
        if st.button("🔎 Open Invoice Details", width="stretch"):
            st.session_state["selected_invoice_id"] = run.invoice_id
            st.session_state["current_page"] = "Invoice Details"
            st.rerun()

    if should_poll(run.execution_status, run.start_time, now_utc(), ui_config.STALE_RUN_MINUTES):
        st.session_state["_live_watching_run_id"] = run.id
        _live_run_fragment(run.id)
    else:
        st.session_state.pop("_live_watching_run_id", None)
        _render_run_details(run)
