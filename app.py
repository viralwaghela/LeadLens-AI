import streamlit as st

from core.auth import require_login
from dashboard import show_dashboard
from onboarding import show_onboarding
from services.platform_data import company_setup_complete

st.set_page_config(
    page_title="LeadLens CareOS",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

if not require_login():
    st.stop()

try:
    has_company = company_setup_complete()
except RuntimeError as error:
    st.error(f"⚠️ LeadLens can't reach its database right now.\n\n{error}")
    st.stop()

if has_company:
    show_dashboard()
else:
    show_onboarding()
