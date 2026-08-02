"""Minimal password gate for the whole app.

This is deliberately simple: one shared password per deployed instance,
kept in `.env` / the host's secrets manager as `APP_PASSWORD`. It is NOT
per-user accounts or multi-tenant login — it's the smallest thing that
stops a dashboard full of real patient/business data from being open to
anyone who finds the URL, which matters the moment this is deployed for
a real paying clinic.

If APP_PASSWORD is left unset, the gate is skipped (so local development
still works with zero setup) but a loud on-screen warning is shown so an
accidentally-unprotected deployment is hard to miss.
"""
from __future__ import annotations

import hmac
import os

import streamlit as st

SESSION_KEY = "app_authenticated"


def _configured_password() -> str:
    return os.getenv("APP_PASSWORD", "").strip()


def require_login() -> bool:
    """Render a login screen if needed. Returns True once access is allowed.

    Call this at the very top of app.py, before any business data is loaded
    or rendered, and stop execution (return) if it returns False.
    """
    password = _configured_password()

    if not password:
        st.warning(
            "⚠️ No APP_PASSWORD is set — this dashboard is currently open to "
            "anyone with the URL. Set APP_PASSWORD in your .env file (or your "
            "hosting platform's secrets manager) before using this with real "
            "clinic data.",
            icon="⚠️",
        )
        return True

    if st.session_state.get(SESSION_KEY):
        return True

    st.markdown(
        "<div style='max-width:420px;margin:8rem auto 0;text-align:center'>"
        "<h2>🔒 LeadLens CareOS</h2>"
        "<p style='opacity:.7'>Enter the access password to continue.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.4, 1])
    with center:
        with st.form("login_form"):
            entered = st.text_input("Password", type="password", label_visibility="collapsed", placeholder="Password")
            submitted = st.form_submit_button("Unlock", use_container_width=True, type="primary")
        if submitted:
            if hmac.compare_digest(entered.strip(), password):
                st.session_state[SESSION_KEY] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


def render_logout_control() -> None:
    """Optional small logout button — call from the sidebar once logged in."""
    if not _configured_password():
        return
    if st.session_state.get(SESSION_KEY) and st.button("Log out", key="app_logout_btn", use_container_width=True):
        st.session_state[SESSION_KEY] = False
        st.rerun()
