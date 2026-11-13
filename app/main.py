"""
AI-Powered Accounts Payable (AP) Automation & Decision Engine.
Main Streamlit application entry point.
"""
import streamlit as st
from app.config.settings import settings
from app.database.db import init_db, get_db
from app.ui.dashboard import render_dashboard
from app.ui.upload import render_upload
from app.ui.processing_view import render_processing_view
from app.ui.invoice_history import render_invoice_history
from app.ui.invoice_details import render_invoice_details
from app.ui.settings_view import render_settings

# Configure page layout and title
st.set_page_config(
    page_title="Zamp AP Automation & Decision Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize database tables on startup
init_db()


def main():
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
        # System status indicator in sidebar
        if settings.has_live_llm:
            st.success(f"🟢 **AI Extraction:** Live ({settings.LLM_MODEL})")
        else:
            st.info("🟡 **AI Extraction:** Demo / Rule Engine")

        st.caption(f"DB: SQLite | OCR: {'Enabled' if settings.OCR_ENABLED else 'Disabled'}")

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
        elif selected_page == "Settings & Rules":
            render_settings(session)


if __name__ == "__main__":
    main()
