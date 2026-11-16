"""
Streamlit Settings View: Inspection of business thresholds, AI providers, and system operations.
"""
import streamlit as st
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.config.rules import rules_config
from app.ingestion.ocr_engine import get_ocr_engine
from app.database.entities import Vendor, PurchaseOrder, Invoice
from scripts.seed_database import seed


def render_settings(session: Session):
    st.markdown("## ⚙️ AP Policy Configuration & Engine Settings")
    st.caption("Inspect and manage business thresholds, tolerances, and extraction providers.")

    # 1. AI Provider Status
    st.subheader("AI Extraction Provider Status")
    col1, col2 = st.columns(2)
    with col1:
        if settings.has_live_llm:
            st.success("🟢 **Live LLM Provider Active**")
            st.write(f"- **Base URL:** `{settings.LLM_BASE_URL}`")
            st.write(f"- **Model:** `{settings.LLM_MODEL}`")
        else:
            st.warning("🟡 **Offline / Demo Mode Active**")
            st.caption("No `LLM_API_KEY` configured in `.env`. Running deterministic extraction engine.")
    with col2:
        ocr_engine = get_ocr_engine()
        ocr_status = "Tesseract ready" if ocr_engine.is_available() else "Unavailable"
        st.write(f"- **OCR Runtime:** `{ocr_status}`")
        st.write(f"- **Environment:** `{settings.APP_ENV}`")
        st.write(f"- **Database URL:** `{settings.DATABASE_URL}`")

    st.markdown("---")

    # 2. Configurable Business Tolerances & Rules
    st.subheader("Financial Validation & Matching Tolerances")

    with st.form("settings_form"):
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            arith_tol = st.number_input(
                "Arithmetic & Rounding Tolerance ($)",
                value=float(rules_config.financial.ARITHMETIC_TOLERANCE_ABS),
                min_value=0.00,
                max_value=1.00,
                step=0.01,
                help="Allowed absolute variance for rounding differences in totals."
            )
            near_dup_days = st.number_input(
                "Near-Duplicate Date Window (Days)",
                value=int(rules_config.duplicates.NEAR_DUPLICATE_DAYS_WINDOW),
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
                value=float(rules_config.matching.VENDOR_HIGH_CONFIDENCE_THRESHOLD),
                step=0.01,
                help="Minimum similarity score required to confidently auto-match a vendor name."
            )
            min_conf = st.slider(
                "Minimum Extraction Confidence Threshold",
                min_value=0.50,
                max_value=1.00,
                value=float(rules_config.extraction.MIN_OVERALL_CONFIDENCE_THRESHOLD),
                step=0.01,
                help="Invoices with overall extraction confidence below this score are sent for human review."
            )

        save_settings = st.form_submit_button("💾 Save Threshold Parameters", type="primary")
        if save_settings:
            rules_config.financial.ARITHMETIC_TOLERANCE_ABS = arith_tol
            rules_config.duplicates.NEAR_DUPLICATE_DAYS_WINDOW = near_dup_days
            rules_config.matching.VENDOR_HIGH_CONFIDENCE_THRESHOLD = vendor_thresh
            rules_config.extraction.MIN_OVERALL_CONFIDENCE_THRESHOLD = min_conf
            st.success("Thresholds and business parameters updated successfully for the current session.")

    st.markdown("---")

    # 3. Database Maintenance & Reset
    st.subheader("Database Operations")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.write(f"- Total Vendors: **{session.query(Vendor).count()}**")
        st.write(f"- Total Purchase Orders: **{session.query(PurchaseOrder).count()}**")
        st.write(f"- Total Invoices: **{session.query(Invoice).count()}**")

    with col_d2:
        if st.button("🔄 Reset & Reseed Database", type="secondary"):
            # Clear existing invoices and dependent tables in proper order
            from app.database.entities import InvoiceLineItem, ValidationResultEntity, AuditLog, ProcessingRun, POLineItem
            session.query(InvoiceLineItem).delete()
            session.query(ValidationResultEntity).delete()
            session.query(AuditLog).delete()
            session.query(ProcessingRun).delete()
            session.query(Invoice).delete()
            session.query(POLineItem).delete()
            session.query(PurchaseOrder).delete()
            session.query(Vendor).delete()
            session.commit()
            seed()
            st.success("Database reseeded successfully with clean sample procurement data.")
            st.rerun()
