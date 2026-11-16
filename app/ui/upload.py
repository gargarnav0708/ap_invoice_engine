"""
Streamlit Invoice Upload View: Drag-and-drop file upload and 1-click test scenario
runners, with a true live run view (each of the 19 stages ticking over as it
executes) and a final decision card showing the reasoned outcome.
"""
import io

import streamlit as st
from sqlalchemy.orm import Session
from PIL import Image

from app.config.settings import settings
from app.workflow.orchestrator import WorkflowOrchestrator
from app.ingestion.ocr_engine import get_ocr_engine


# Canonical ordered list of the 19 pipeline stages, shown greyed-out before they run.
PIPELINE_STAGES = [
    "1. Receive invoice",
    "2. Validate uploaded file",
    "3. Calculate file hash",
    "4. Check for exact duplicate",
    "5. Extract document text",
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
    st.caption("Upload vendor PDF or image invoices for OCR, AI extraction, 3-way PO matching, and deterministic validation.")

    orchestrator = WorkflowOrchestrator(session)

    # The dataset tab is opt-in so opening the app never triggers a dataset download.
    tab_upload, tab_scenarios, tab_dataset = st.tabs([
        "Upload Document",
        "Quick Test Scenarios (1-Click)",
        "OCR Dataset Check",
    ])

    with tab_upload:
        st.subheader("Upload Invoice Document")
        uploaded_file = st.file_uploader(
            "Choose a PDF or invoice image",
            type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"],
        )

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
            ("Scenario 5: Low-Quality Scan",
             "A blurred raster scan of an incomplete invoice exercises image OCR and safe review.",
             "NEEDS_REVIEW (OCR / Missing Fields)", "scenario_5_scanned_low_quality.png", "btn_s5"),
            ("Scenario 6: Ambiguous PO",
             "Known vendor with no PO reference and multiple plausible open orders.",
             "NEEDS_REVIEW (Ambiguous PO)", "scenario_6_ambiguous_po.pdf", "btn_s6"),
            ("Scenario 7: Changed-PDF Near Duplicate",
             "Same vendor and amount within seven days, but a different file and invoice number. Run Scenario 1 first.",
             "NEEDS_REVIEW (Potential Duplicate)", "scenario_7_changed_pdf_near_duplicate.pdf", "btn_s7"),
            ("Scenario 8: Small Tolerance Variance",
             "A $0.04 arithmetic variance and 0.5% unit-price delta stay within configured tolerances.",
             "APPROVED (Within Tolerance)", "scenario_8_small_tolerance_variance.pdf", "btn_s8"),
        ]

        st.caption("For a repeatable demo, start with a fresh disposable database. Run Scenario 1 before Scenario 7 to establish the first invoice for near-duplicate comparison.")
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
        samples.append({
            "id": f"sample-{row_index + 1}",
            "image": buffer.getvalue(),
        })
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
    metric_a, metric_b = st.columns(2)
    metric_a.metric("Dataset images", "8,181")
    metric_b.metric("Loaded per check", "3")
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
            st.image(sample["image"], use_container_width=True)
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
