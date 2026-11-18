"""
AI-Powered Accounts Payable (AP) Automation & Decision Engine.
Main Streamlit application entry point.
"""
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st
from app.config.settings import settings
from app.config.persistence import ConfigurationService
from app.database.db import engine, init_db, get_db
from app.utils.logging import get_logger
from app.ingestion.ocr_engine import get_ocr_engine
from app.ui.dashboard import render_dashboard
from app.ui.upload import render_upload
from app.ui.processing_view import render_processing_view
from app.ui.invoice_history import render_invoice_history
from app.ui.invoice_details import render_invoice_details
from app.ui.procurement_view import render_procurement_view
from app.ui.settings_view import render_settings
from app.ui.theme import apply_theme, render_theme_selector

logger = get_logger("main")

# Configure page layout and title
st.set_page_config(
    page_title="Zamp AP Automation & Decision Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)


def _apply_secrets() -> None:
    """Bridge Streamlit secrets (used on Streamlit Community Cloud) into the
    settings object so a deployed app can use live LLM extraction without a
    committed .env. Env vars still take priority when already set."""
    try:
        secrets = st.secrets
    except Exception:
        return
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        try:
            val = secrets.get(key) if hasattr(secrets, "get") else secrets[key]
        except Exception:
            val = None
        if val:
            setattr(settings, key, str(val))


@st.cache_resource
def _bootstrap() -> bool:
    """One-time startup: create tables, seed procurement data, and generate the
    sample invoice fixtures if they are missing. Cached so it runs once per
    server process (safe for a fresh container on Streamlit Cloud)."""
    init_db()
    try:
        from app.database.db import get_db
        from app.database.entities import Vendor
        with get_db() as session:
            vendor_count = session.query(Vendor).count()
            cfg = ConfigurationService(session)
            cfg.load_rule_thresholds()
            cfg.load_usd_to_inr_rate()
        if vendor_count == 0:
            from scripts.seed_database import seed
            seed()
            logger.info("Bootstrap: seeded procurement database.")
    except Exception as e:
        logger.error(f"Bootstrap seeding failed: {e}")

    try:
        from scripts.generate_test_invoices import ensure_scenario_fixtures
        ensure_scenario_fixtures()
        logger.info("Bootstrap: verified sample invoice fixtures.")
    except Exception as e:
        logger.error(f"Bootstrap fixture generation failed: {e}")
    return True


_apply_secrets()
_bootstrap()


def main():
    with get_db() as config_session:
        cfg_service = ConfigurationService(config_session)
        extraction_mode = cfg_service.load_extraction_preferences().mode
        cfg_service.load_usd_to_inr_rate()

    # Sidebar Navigation & Branding
    with st.sidebar:
        st.markdown("# ⚡ **Zamp AP Engine**")
        st.caption("AI-Powered Accounts Payable Automation")
        st.markdown("---")

        nav_options = [
            "Overview Dashboard",
            "Upload Invoice",
            "Live Processing View",
            "Invoice History",
            "Invoice Details",
            "Vendors & Purchase Orders",
            "Settings & Rules"
        ]

        if "current_page" not in st.session_state:
            st.session_state["current_page"] = "Overview Dashboard"

        current_index = nav_options.index(st.session_state["current_page"]) if st.session_state["current_page"] in nav_options else 0

        selected_page = st.radio(
            "Navigation",
            options=nav_options,
            index=current_index,
            label_visibility="collapsed"
        )
        st.session_state["current_page"] = selected_page

        st.markdown("---")
        # Per-session appearance preference (never persisted server-side).
        theme = render_theme_selector()
        st.markdown("---")
        # System status indicator in sidebar (theme-aware card)
        if extraction_mode == "live":
            st.html(
                f'<div class="ap-decision ap-decision--success" role="status">'
                f'<div class="ap-decision__title">🟢 AI Extraction: Live</div>'
                f'<div class="ap-decision__body"><code>{settings.LLM_MODEL}</code></div></div>'
            )
        else:
            st.html(
                '<div class="ap-decision ap-decision--info" role="status">'
                '<div class="ap-decision__title">🟡 AI Extraction: Demo</div>'
                '<div class="ap-decision__body">Deterministic rule engine</div></div>'
            )

        ocr_engine = get_ocr_engine()
        ocr_status = "Tesseract ready" if ocr_engine.is_available() else "Unavailable"
        db_status = "PostgreSQL" if engine.dialect.name == "postgresql" else engine.dialect.name.title()
        st.caption(f"DB: {db_status} | OCR: {ocr_status}")

    apply_theme(theme)

    # Main Content Area
    with get_db() as session:
        if selected_page == "Overview Dashboard":
            render_dashboard(session)
        elif selected_page == "Upload Invoice":
            render_upload(session)
        elif selected_page == "Live Processing View":
            render_processing_view(session)
        elif selected_page == "Invoice History":
            render_invoice_history(session)
        elif selected_page == "Invoice Details":
            render_invoice_details(session)
        elif selected_page == "Vendors & Purchase Orders":
            render_procurement_view(session)
        elif selected_page == "Settings & Rules":
            render_settings(session)


if __name__ == "__main__":
    main()
