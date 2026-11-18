"""
Streamlit Settings View: Inspection of business thresholds, AI providers, and system operations.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Mapping

import streamlit as st
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import ui as ui_config
from app.config.persistence import ConfigurationService
from app.config.rules import EditableRuleThresholds
from app.config.settings import settings
from app.database.entities import Invoice, PurchaseOrder, Vendor
from app.database.maintenance import reset_transactional_data
from app.ingestion.ocr_engine import get_ocr_engine
from app.processing.currency import all_rates_to_inr
from app.ui.components import render_kpis, render_table
from app.ui.presentation import Kpi
from scripts.seed_database import seed

_DEPLOYED_ENV_MARKERS: tuple[str, ...] = (
    "RENDER",
    "RENDER_SERVICE_ID",
    "RENDER_EXTERNAL_URL",
    "STREAMLIT_SHARING_MODE",
    "DYNO",
    "K_SERVICE",
    "FLY_APP_NAME",
)


def is_local_test_runner_allowed(
    app_env: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return True only in local development/test environments (never on Render/cloud)."""
    env_map = os.environ if environ is None else environ
    effective_env = (app_env if app_env is not None else settings.APP_ENV).strip().lower()
    if effective_env not in {"development", "test"}:
        return False
    flag = env_map.get("ENABLE_UI_TEST_RUNNER", ui_config.TEST_RUNNER_ENV_FLAG).strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    for marker in _DEPLOYED_ENV_MARKERS:
        if env_map.get(marker):
            return False
    return True


def truncate_test_output(output: str, max_chars: int = ui_config.TEST_RUNNER_MAX_OUTPUT_CHARS) -> str:
    """Cap displayed pytest output so large logs cannot overwhelm the browser."""
    if len(output) <= max_chars:
        return output
    half = max(200, (max_chars - 80) // 2)
    return (
        output[:half]
        + f"\n\n... [truncated {len(output) - max_chars:,} characters] ...\n\n"
        + output[-half:]
    )


def run_local_pytest_suite(
    timeout_seconds: int = ui_config.TEST_RUNNER_TIMEOUT_SECONDS,
    max_output_chars: int = ui_config.TEST_RUNNER_MAX_OUTPUT_CHARS,
) -> tuple[int, str]:
    """Execute the fixed local pytest suite safely without shell interpolation."""
    if not is_local_test_runner_allowed():
        raise PermissionError("The in-app test runner is disabled outside local development/test environments.")

    clean_env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"LLM_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"}
    }
    clean_env["APP_ENV"] = "test"
    clean_env["PYTHONUNBUFFERED"] = "1"

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(settings.PROJECT_ROOT),
            env=clean_env,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        combined = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        return int(completed.returncode), truncate_test_output(combined.strip(), max_output_chars)
    except subprocess.TimeoutExpired as exc:
        partial = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
            ("\n" + exc.stderr) if isinstance(exc.stderr, str) and exc.stderr else ""
        )
        msg = f"Test run timed out after {timeout_seconds}s.\n{partial}".strip()
        return 124, truncate_test_output(msg, max_output_chars)


def render_settings(session: Session):
    st.markdown("## ⚙️ AP Policy Configuration & Engine Settings")
    st.caption("Inspect and manage business thresholds, tolerances, and extraction providers.")
    config_service = ConfigurationService(session)
    thresholds = config_service.load_rule_thresholds()
    extraction_preferences = config_service.load_extraction_preferences()
    current_usd_inr = config_service.load_usd_to_inr_rate()

    # 1. AI Provider Status and runtime choice (preserved from Phase 1)
    st.subheader("Extraction Mode")
    col1, col2 = st.columns(2)
    with col1:
        mode_options = ["demo", "live"] if settings.has_live_llm else ["demo"]
        selected_mode = st.radio(
            "Runtime extraction provider",
            options=mode_options,
            index=mode_options.index(extraction_preferences.mode)
            if extraction_preferences.mode in mode_options
            else 0,
            format_func=lambda mode: (
                "Demo / Offline (deterministic)" if mode == "demo" else "Live LLM"
            ),
            help="This selection stores only the mode. Credentials remain in environment configuration.",
        )
        if not settings.has_live_llm:
            st.caption(
                "Live mode is unavailable because `LLM_API_KEY` is not configured. "
                "Set it in `.env` locally or in Render environment variables; never enter it here."
            )
    with col2:
        if extraction_preferences.mode == "live":
            st.success(f"🟢 **Live LLM selected:** `{settings.LLM_MODEL}`")
        else:
            st.info("🟡 **Demo / Offline extraction selected**")
        ocr_engine = get_ocr_engine()
        ocr_status = "Tesseract ready" if ocr_engine.is_available() else "Unavailable"
        st.write(f"- **OCR Runtime:** `{ocr_status}`")
        st.write(f"- **Environment:** `{settings.APP_ENV}`")

    st.markdown("---")

    # 2. Configurable Business Tolerances & Rules
    st.subheader("Financial Validation & Matching Tolerances")

    with st.form("settings_form"):
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            arith_tol = st.number_input(
                "Arithmetic & Rounding Tolerance ($)",
                value=thresholds.arithmetic_tolerance_abs,
                min_value=0.00,
                max_value=1.00,
                step=0.01,
                help="Allowed absolute variance for rounding differences in totals.",
            )
            near_dup_days = st.number_input(
                "Near-Duplicate Date Window (Days)",
                value=thresholds.near_duplicate_days_window,
                min_value=1,
                max_value=60,
                step=1,
                help="Window within which identical vendor amounts trigger near-duplicate warnings.",
            )
            usd_to_inr = st.number_input(
                "USD → INR Exchange Rate (₹ per $1 USD)",
                value=float(current_usd_inr),
                min_value=1.00,
                max_value=500.00,
                step=0.25,
                format="%.2f",
                help="Conversion rate used to convert USD invoices and POs into Indian Rupees (INR ₹) on the dashboard and during cross-currency PO matching.",
            )

        with col_t2:
            vendor_thresh = st.slider(
                "Vendor Fuzzy Match Threshold",
                min_value=0.50,
                max_value=1.00,
                value=thresholds.vendor_high_confidence_threshold,
                step=0.01,
                help="Minimum similarity score required to confidently auto-match a vendor name.",
            )
            min_conf = st.slider(
                "Minimum Extraction Confidence Threshold",
                min_value=0.50,
                max_value=1.00,
                value=thresholds.min_overall_confidence_threshold,
                step=0.01,
                help="Invoices with overall extraction confidence below this score are sent for human review.",
            )

        save_settings = st.form_submit_button("💾 Save Settings", type="primary")
        if save_settings:
            try:
                validated = EditableRuleThresholds(
                    arithmetic_tolerance_abs=arith_tol,
                    near_duplicate_days_window=near_dup_days,
                    vendor_high_confidence_threshold=vendor_thresh,
                    min_overall_confidence_threshold=min_conf,
                )
                config_service.save_rule_thresholds(validated)
                config_service.save_extraction_preferences(selected_mode)
                config_service.save_usd_to_inr_rate(usd_to_inr)
                session.commit()
                st.success("Settings saved. They will survive application restarts.")
            except (ValidationError, ValueError) as exc:
                session.rollback()
                st.error(f"Settings were not saved: {exc}")

    with st.expander("View active currency conversion rates to Indian Rupees (INR ₹)", expanded=False):
        rate_rows = [
            {"Currency": code, "1 Unit in INR (₹)": f"₹{rate:,.2f}"}
            for code, rate in all_rates_to_inr().items()
        ]
        render_table(
            rate_rows,
            ["Currency", "1 Unit in INR (₹)"],
            caption="Active FX conversion rates to INR",
            numeric_columns=["1 Unit in INR (₹)"],
        )

    st.markdown("---")

    # 3. Database Maintenance & Reset
    st.subheader("Database Operations")
    vendor_total = session.query(Vendor).count()
    po_total = session.query(PurchaseOrder).count()
    invoice_total = session.query(Invoice).count()
    render_kpis(
        [
            Kpi("Registered Vendors", f"{vendor_total:,}", "Master vendor records"),
            Kpi("Purchase Orders", f"{po_total:,}", "Open & closed POs"),
            Kpi("Processed Invoices", f"{invoice_total:,}", "Transactional records"),
        ],
        label="Database record counts",
    )

    reset_allowed = settings.APP_ENV.strip().lower() in {"development", "test"}
    confirm_reset = st.checkbox(
        "I understand this deletes local demo transaction data",
        disabled=not reset_allowed,
    )
    if not reset_allowed:
        st.info("Reset is disabled in deployed and production environments.")
    if st.button(
        "🔄 Reset & Reseed Database",
        type="secondary",
        disabled=not (reset_allowed and confirm_reset),
    ):
        reset_transactional_data(session)
        session.commit()
        seed()
        st.success("Database reseeded successfully with clean sample procurement data.")
        st.rerun()

    st.markdown("---")

    # 4. Local-only Developer Diagnostics (disabled in deployed/production environments)
    st.subheader("Developer Diagnostics")
    runner_allowed = is_local_test_runner_allowed()
    if not runner_allowed:
        st.caption(
            "In-app test execution is disabled in deployed and production environments. "
            "Run `pytest -q` from a local terminal or VS Code."
        )
    else:
        st.caption(
            "Local development only: executes the fixed command `python -m pytest -q` "
            f"(timeout {ui_config.TEST_RUNNER_TIMEOUT_SECONDS}s, output capped at "
            f"{ui_config.TEST_RUNNER_MAX_OUTPUT_CHARS:,} chars)."
        )
        if st.button("🧪 Run Automated Test Suite (pytest -q)", type="secondary"):
            with st.spinner("Running local pytest suite…"):
                code, output = run_local_pytest_suite()
            if code == 0:
                st.success("All automated tests passed (exit code 0).")
            else:
                st.error(f"Test suite finished with exit code {code}.")
            st.code(output or "(no output)", language="text")
