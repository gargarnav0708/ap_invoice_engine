"""
Streamlit Settings View: Inspection of business thresholds, AI providers, and system operations.
"""
import streamlit as st
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.config.rules import EditableRuleThresholds
from app.config.persistence import ConfigurationService
from app.database.maintenance import reset_transactional_data
from app.ingestion.ocr_engine import get_ocr_engine
from app.database.entities import Vendor, PurchaseOrder, Invoice
from scripts.seed_database import seed


def render_settings(session: Session):
    st.markdown("## ⚙️ AP Policy Configuration & Engine Settings")
    st.caption("Inspect and manage business thresholds, tolerances, and extraction providers.")
    config_service = ConfigurationService(session)
    thresholds = config_service.load_rule_thresholds()
    extraction_preferences = config_service.load_extraction_preferences()

    # 1. AI Provider Status and runtime choice
    st.subheader("Extraction Mode")
    col1, col2 = st.columns(2)
    with col1:
        mode_options = ["demo", "live"] if settings.has_live_llm else ["demo"]
        selected_mode = st.radio(
            "Runtime extraction provider",
            options=mode_options,
            index=mode_options.index(extraction_preferences.mode),
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
                help="Allowed absolute variance for rounding differences in totals."
            )
            near_dup_days = st.number_input(
                "Near-Duplicate Date Window (Days)",
                value=thresholds.near_duplicate_days_window,
                min_value=1,
                max_value=60,
                step=1,
                help="Window within which identical vendor amounts trigger near-duplicate warnings."
            )

        with col_t2:
            vendor_thresh = st.slider(
                "Vendor Fuzzy Match Threshold",
                min_value=0.50,
                max_value=1.00,
                value=thresholds.vendor_high_confidence_threshold,
                step=0.01,
                help="Minimum similarity score required to confidently auto-match a vendor name."
            )
            min_conf = st.slider(
                "Minimum Extraction Confidence Threshold",
                min_value=0.50,
                max_value=1.00,
                value=thresholds.min_overall_confidence_threshold,
                step=0.01,
                help="Invoices with overall extraction confidence below this score are sent for human review."
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
                session.commit()
                st.success("Settings saved. They will survive application restarts.")
            except (ValidationError, ValueError) as exc:
                session.rollback()
                st.error(f"Settings were not saved: {exc}")

    st.markdown("---")

    # 3. Database Maintenance & Reset
    st.subheader("Database Operations")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.write(f"- Total Vendors: **{session.query(Vendor).count()}**")
        st.write(f"- Total Purchase Orders: **{session.query(PurchaseOrder).count()}**")
        st.write(f"- Total Invoices: **{session.query(Invoice).count()}**")

    with col_d2:
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
