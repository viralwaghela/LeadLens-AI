import streamlit as st

from core.auth import require_login
from core.demo_mode import DEMO_DISABLED_MESSAGE, DEMO_ERROR_MESSAGE, DemoModeError, demo_mode_enabled
from dashboard import show_dashboard
from onboarding import show_onboarding
from services.platform_data import company_setup_complete

st.set_page_config(
    page_title="LeadLens CareOS",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

if demo_mode_enabled():
    # Public demo deployment (docs/V2_DEMO_ENVIRONMENT.md): a server-side,
    # passwordless entry into the demo tenant instead of the login form.
    from ui.demo_entry import render_demo_banner, require_demo_session

    if not require_demo_session():
        st.stop()
    render_demo_banner()
elif not require_login():
    st.stop()

try:
    has_company = company_setup_complete()
except RuntimeError as error:
    st.error(f"⚠️ LeadLens can't reach its database right now.\n\n{error}")
    st.stop()

if demo_mode_enabled():
    # Never onboarding in the demo (it writes); a missing profile just means "not seeded".
    if not has_company:
        st.info("The demo is being prepared. Please check back shortly.")
        st.stop()
    from services.authorization_guard import PermissionDenied

    try:
        show_dashboard()
    except (DemoModeError, PermissionDenied):
        # Every write/approve/send/export is refused server-side; this only makes the
        # refusal read as a message instead of an app error.
        st.info(DEMO_DISABLED_MESSAGE)
    except Exception:  # noqa: BLE001 - a public visitor must never see a crash page
        # E.g. submitting an empty write form raises a validation error deep in the page.
        # Streamlit Cloud would show a redacted "app has encountered an error" box; show a
        # calm message instead and keep the details in the log. (Streamlit's own rerun /
        # stop control-flow exceptions are BaseExceptions and pass straight through.)
        import logging

        logging.getLogger(__name__).exception("public demo: a page raised")
        st.info(DEMO_ERROR_MESSAGE)
elif has_company:
    show_dashboard()
else:
    show_onboarding()
