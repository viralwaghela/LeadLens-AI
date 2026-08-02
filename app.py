import streamlit as st

from core.auth import require_login
from core.memory import company_exists
from dashboard import show_dashboard
from onboarding import show_onboarding

st.set_page_config(
    page_title="LeadLens CareOS",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

if not require_login():
    st.stop()

if company_exists():
    show_dashboard()
else:
    show_onboarding()
