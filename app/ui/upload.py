"""
Streamlit Invoice Upload View: Drag-and-drop file upload and 1-click test scenario
runners, with a true live run view (each of the 19 stages ticking over as it
executes) and a final decision card showing the reasoned outcome.
"""
import io
from typing import Any

import streamlit as st
from PIL import Image
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.ingestion.ocr_engine import get_ocr_engine
from app.processing.currency import convert_to_inr, format_inr, format_native_and_inr
from app.ui.components import render_kpis, render_status_banner, render_table
from app.ui.presentation import Kpi, badge_html, invoice_display_status, status_meta
from app.workflow.batch_runner import run_invoice_batch
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.stages import PIPELINE_STAGES

_STATUS_ICON = {
    "PENDING": "⚪",
    "RUNNING": "⏳",
    "COMPLETED": "✅",
    "FAILED": "❌",
}


def render_upload(session: Session):
    st.markdown("## 📥 Ingest & Process Invoices")
    st.caption(
        "Upload vendor PDF or image invoices for OCR, AI extraction, 3-way PO matching, and deterministic validation."
    )

    orchestrator = WorkflowOrchestrator(session)

    # The dataset tab is opt-in so opening the app never triggers a dataset download.
    tab_upload, tab_scenarios, tab_dataset = st.tabs(
        [
            "Upload Document",
            "Quick Test Scenarios (1-Click)",
            "OCR Dataset Check",
        ]
    )

    with tab_upload:
        st.subheader("Upload Invoice Document(s)")
        uploaded_files = st.file_uploader(
            "Choose one or more PDF or invoice image files (batch processing supported)",
            type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"],
            accept_multiple_files=True,
        )

        if uploaded_files:
            if len(uploaded_files) == 1:
                single = uploaded_files[0]
                col_info1, col_info2 = st.columns(2)
                with col_info1:
                    st.write(f"**Filename:** `{single.name}`")
                with col_info2:
                    st.write(f"**File Size:** `{len(single.getvalue()) / 1024:.1f} KB`")

                if st.button("🚀 Process Invoice Now", type="primary", width="stretch"):
                    _run_with_live_view(
                        orchestrator,
                        file_bytes=single.getvalue(),
                        filename=single.name,
                        title=single.name,
                    )
            else:
                total_kb = sum(len(f.getvalue()) for f in uploaded_files) / 1024.0
                st.write(
                    f"**Selected Batch:** `{len(uploaded_files)}` documents (`{total_kb:.1f} KB` total) — "
                    "processed with per-document error isolation."
                )
                if st.button(
                    f"🚀 Process Batch ({len(uploaded_files)} Invoices)",
                    type="primary",
                    width="stretch",
                ):
                    _run_batch_upload(
                        orchestrator,
                        [(f.name, f.getvalue()) for f in uploaded_files],
                    )

    with tab_scenarios:
        st.subheader("Run Verified Case Study Scenarios")
        st.write("Demonstrate the core AP engine capabilities on synthetic benchmark fixtures:")

        scenarios = [
            (
                "Scenario 1: Clean Invoice",
                "Approved vendor ('Acme Industrial Supplies'), valid PO ('PO-2026-001'), matching line items and math.",
                "APPROVED",
                "APPROVED",
                "scenario_1_clean_invoice.pdf",
                "btn_s1",
            ),
            (
                "Scenario 2: Duplicate Invoice",
                "Re-submission of the identical clean invoice file / invoice number.",
                "BLOCKED",
                "BLOCKED (Confirmed Duplicate)",
                "scenario_2_duplicate_invoice.pdf",
                "btn_s2",
            ),
            (
                "Scenario 3: Split PO Overbilling",
                "PO-2026-002 has only $500 remaining balance. Invoice bills $1,100, exceeding available balance.",
                "BLOCKED",
                "BLOCKED (PO Balance Exceeded)",
                "scenario_3_split_overbilling.pdf",
                "btn_s3",
            ),
            (
                "Scenario 4: Incomplete / Unreadable Invoice",
                "Document missing invoice number, PO reference, and clear tax arithmetic.",
                "NEEDS_REVIEW",
                "NEEDS_REVIEW (Missing Critical Fields)",
                "scenario_4_incomplete_invoice.pdf",
                "btn_s4",
            ),
            (
                "Scenario 5: Low-Quality Scan",
                "A blurred raster scan of an incomplete invoice exercises image OCR and safe review.",
                "NEEDS_REVIEW",
                "NEEDS_REVIEW (OCR / Missing Fields)",
                "scenario_5_scanned_low_quality.png",
                "btn_s5",
            ),
            (
                "Scenario 6: Ambiguous PO",
                "Known vendor with no PO reference and multiple plausible open orders.",
                "NEEDS_REVIEW",
                "NEEDS_REVIEW (Ambiguous PO)",
                "scenario_6_ambiguous_po.pdf",
                "btn_s6",
            ),
            (
                "Scenario 7: Changed-PDF Near Duplicate",
                "Same vendor and amount within seven days, but a different file and invoice number. Run Scenario 1 first.",
                "NEEDS_REVIEW",
                "NEEDS_REVIEW (Potential Duplicate)",
                "scenario_7_changed_pdf_near_duplicate.pdf",
                "btn_s7",
            ),
            (
                "Scenario 8: Small Tolerance Variance",
                "A $0.04 arithmetic variance and 0.5% unit-price delta stay within configured tolerances.",
                "APPROVED",
                "APPROVED (Within Tolerance)",
                "scenario_8_small_tolerance_variance.pdf",
                "btn_s8",
            ),
        ]

        st.caption(
            "For a repeatable demo, start with a fresh disposable database. "
            "Run Scenario 1 before Scenario 7 to establish the first invoice for near-duplicate comparison."
        )
        col1, col2 = st.columns(2)
        for idx, (name, desc, status_key, expected, filename, key) in enumerate(scenarios):
            target = col1 if idx % 2 == 0 else col2
            with target:
                st.markdown(f"### {name}")
                st.caption(desc)
                st.html(f"<div>🎯 <strong>Expected Outcome:</strong> {badge_html(status_key, size='sm')} <code>{expected}</code></div>")
                if st.button(f"▶ Run {name.split(':')[0]}", key=key, width="stretch"):
                    st.session_state["_pending_scenario"] = (filename, name)
                st.markdown("---")

    with tab_dataset:
        _render_dataset_check()

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


@st.cache_data(ttl=3600, show_spinner=False)
def _load_dataset_image_samples(limit: int = 3):
    """Fetch a small image sample from HF using streaming (never full-download)."""
    from datasets import load_dataset

    dataset = load_dataset(
        "Voxel51/high-quality-invoice-images-for-ocr",
        split="train",
        streaming=True,
    )
    samples = []
    for row_index, row in enumerate(dataset):
        image_value = row.get("image")
        if image_value is None:
            continue

        if isinstance(image_value, dict):
            image_bytes = image_value.get("bytes")
            if image_bytes is None and image_value.get("path"):
                with open(image_value["path"], "rb") as image_file:
                    image_bytes = image_file.read()
            if image_bytes is None:
                continue
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        else:
            image = image_value.convert("RGB")

        buffer = io.BytesIO()
        image.thumbnail((900, 1200))
        image.save(buffer, format="JPEG", quality=88)
        samples.append(
            {
                "id": f"sample-{row_index + 1}",
                "image": buffer.getvalue(),
            }
        )
        if len(samples) >= limit:
            break
    return samples


def _render_dataset_check() -> None:
    st.subheader("Image OCR sample gallery")
    st.write(
        "Load a few invoice images from the Voxel51 dataset and run the configured OCR "
        "engine on each one. Review the recognized text here; this check does not create "
        "or change invoice records."
    )
    st.caption(
        "The Hugging Face `load_dataset` imagefolder view exposes image rows. Samples are "
        "synthetic and fetched only on request using streaming, not as a full dataset download."
    )
    render_kpis(
        [
            Kpi("Dataset images", "8,181", "Voxel51 invoice image corpus"),
            Kpi("Loaded per check", "3", "Streamed on demand"),
        ],
        label="OCR dataset metrics",
    )
    st.caption(
        "The dataset card describes OCR annotations, but the `load_dataset` imagefolder view "
        "returns image-only rows. This tab is a visual OCR smoke check; it does not calculate "
        "CER/WER against the separate annotation sidecar."
    )
    st.markdown(
        "[Open the Voxel51 dataset card](https://huggingface.co/datasets/"
        "Voxel51/high-quality-invoice-images-for-ocr)"
    )

    if st.button("Load 3 invoice images", key="load_ocr_dataset"):
        try:
            with st.spinner("Streaming annotated samples from Hugging Face…"):
                st.session_state["_ocr_dataset_samples"] = _load_dataset_image_samples(3)
            st.session_state["_ocr_dataset_results"] = {}
        except ImportError:
            st.error("The `datasets` package is missing. Install the app dependencies and try again.")
        except Exception as exc:
            st.error(f"Could not load the Hugging Face dataset: {exc}")

    samples = st.session_state.get("_ocr_dataset_samples", [])
    if not samples:
        return

    engine = get_ocr_engine()
    if not engine.is_available():
        st.warning("OCR is unavailable on this runtime. Install Tesseract and restart the app to run this check.")

    results = st.session_state.setdefault("_ocr_dataset_results", {})
    cols = st.columns(len(samples))
    for index, (column, sample) in enumerate(zip(cols, samples), start=1):
        with column:
            st.markdown(f"**Sample {index}**")
            st.image(sample["image"], width="stretch")
            if st.button("Run OCR", key=f"dataset_ocr_{sample['id']}", disabled=not engine.is_available()):
                try:
                    image = Image.open(io.BytesIO(sample["image"])).convert("RGB")
                    results[sample["id"]] = engine.extract_text_from_image(image)
                except Exception as exc:
                    results[sample["id"]] = f"__ERROR__{exc}"

            output = results.get(sample["id"])
            if output is not None:
                if output.startswith("__ERROR__"):
                    st.error(output.removeprefix("__ERROR__"))
                else:
                    with st.expander("OCR output"):
                        st.text(output or "(No text recognized)")


def _run_with_live_view(orchestrator: WorkflowOrchestrator, file_bytes: bytes, filename: str, title: str):
    """Executes the pipeline while streaming each stage into a live view, then
    renders the final reasoned decision card."""
    st.markdown(f"### ⚙️ Live Run — {title}")
    stage_placeholder = st.empty()

    stages_order: list[str] = list(PIPELINE_STAGES)
    stage_state: dict[str, dict[str, Any]] = {
        name: {"status": "PENDING", "details": None, "duration_ms": None}
        for name in stages_order
    }

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

    def on_event(event: dict[str, Any]) -> None:
        name = event.get("stage_name")
        if not isinstance(name, str):
            return
        if name not in stage_state:
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
        render_status_banner(
            "FAILED",
            title="Processing Run Failed — Needs Human Review",
            body=str(e),
        )
        return

    st.session_state["selected_invoice_id"] = inv_entity.id
    st.session_state["latest_run_id"] = run_entity.id
    st.session_state["selected_run_id"] = run_entity.id

    _render_decision_card(inv_entity, run_entity)


def _render_decision_card(inv_entity, run_entity):
    """Renders the final reasoned decision with reasons and the recommended next action."""
    decision = getattr(run_entity, "decision_result", None)
    display_status = invoice_display_status(
        inv_entity.final_decision, inv_entity.processing_status, inv_entity.manual_override_decision
    )
    meta = status_meta(display_status)

    st.markdown("### Decision")
    summary_text = decision.summary if decision is not None else meta.description
    render_status_banner(display_status, title=meta.label, body=summary_text)

    render_kpis(
        [
            Kpi("Decision", f"{meta.symbol} {meta.label}", f"Code: {display_status}", meta.tone),
            Kpi(
                "Invoice Total (INR)",
                format_inr(convert_to_inr(inv_entity.total_amount, inv_entity.currency or "USD"))
                if inv_entity.total_amount is not None
                else "—",
                format_native_and_inr(inv_entity.total_amount, inv_entity.currency),
                "info",
            ),
            Kpi("Extraction Confidence", f"{(inv_entity.extraction_confidence or 0) * 100:.0f}%", "Weighted field confidence"),
            Kpi("Processing Time", f"{run_entity.duration_ms:.0f} ms" if run_entity.duration_ms else "—", "19-stage execution"),
        ],
        label="Processed invoice decision metrics",
    )

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


def _run_batch_upload(orchestrator: WorkflowOrchestrator, documents: list[tuple[str, bytes]]) -> None:
    """Process multiple uploaded invoices with per-document error isolation."""
    st.markdown(f"### 📦 Batch Run ({len(documents)} documents)")
    progress = st.progress(0.0, text="Starting batch processing…")

    def _on_item(idx: int, total: int, item) -> None:
        progress.progress(idx / total, text=f"Processed {idx} of {total}: {item.filename} ({item.status})")

    batch = run_invoice_batch(orchestrator, documents, on_item_complete=_on_item)
    last_valid = next((r for r in reversed(batch.results) if r.invoice_id is not None), None)
    if last_valid is not None:
        st.session_state["selected_invoice_id"] = last_valid.invoice_id
        st.session_state["latest_run_id"] = last_valid.run_id
        st.session_state["selected_run_id"] = last_valid.run_id

    render_kpis(
        [
            Kpi("Batch Total", str(batch.total), "Documents submitted", "info"),
            Kpi("Approved", str(batch.approved), "Auto-approved", "success"),
            Kpi("Needs Review", str(batch.needs_review), "Routed to reviewer", "warning"),
            Kpi("Blocked", str(batch.blocked), "Hard control stops", "danger"),
            Kpi("Failed", str(batch.failed), "Isolated errors", "danger" if batch.failed else "neutral"),
        ],
        label="Batch processing summary",
    )

    rows = []
    for item in batch.results:
        rows.append(
            {
                "ID": f"#{item.invoice_id}" if item.invoice_id else "—",
                "Decision": item.status,
                "Filename": item.filename,
                "Invoice #": item.invoice_number or "—",
                "Vendor": item.vendor_name or "Unmatched",
                "Amount": format_native_and_inr(item.total_amount, item.currency),
                "Confidence": f"{(item.confidence or 0) * 100:.0f}%" if item.confidence is not None else "—",
                "Summary / Error": item.error or item.summary,
            }
        )
    render_table(
        rows,
        ["ID", "Decision", "Filename", "Invoice #", "Vendor", "Amount", "Confidence", "Summary / Error"],
        caption="Batch invoice processing outcomes",
        column_kinds={"Decision": "status"},
        wrap_columns=["Summary / Error", "Filename"],
        numeric_columns=["ID", "Amount", "Confidence"],
    )

