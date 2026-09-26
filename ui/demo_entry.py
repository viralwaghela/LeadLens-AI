"""Public-demo automatic entry, safety gate, and workspace banner.

Presentation and flow only. The actual admission decision is made server-
side by core/demo_session.py, and everything the visitor can or cannot do
is enforced below the UI (core/demo_mode.py, core/db/demo_guard.py,
core/identity/permissions.py, ...).

Order of events for a visitor:

    1. Safety gate — the startup tripwire (core/demo_tripwire.py). If the
       deployment looks misconfigured (a production secret in the
       environment, a non-demo database, ...) the visitor sees only a
       generic "unavailable" page; the specifics go to the log, never the
       screen.
    2. If a valid demo session exists (or can be restored from the signed
       reload token the Core switch uses), continue into the product.
    3. Otherwise the server admits the visitor as the demo viewer straight
       away (no landing page, no click, no credentials) and reruns into the
       workspace. If the demo is not seeded/ready the visitor sees a notice.
"""
from __future__ import annotations

import logging

import streamlit as st

from core.demo_session import DemoNotReady, build_demo_session, is_demo_session

_LOG = logging.getLogger(__name__)
_GATE_KEY = "demo_gate_ok"

BANNER_TEXT = "Demo Workspace — Sample Data Only"


def _unavailable(reason: str) -> bool:
    _LOG.error("Public demo unavailable: %s", reason)  # names only, never values — see core/demo_tripwire.py
    st.title("LeadLens CareOS — Public Demo")
    st.info("The demo is temporarily unavailable. Please try again later.")
    return False


def _safety_gate() -> bool:
    """The tripwire. Environment checks run on every rerun (cheap); the
    database checks run once per browser session."""
    from core.demo_tripwire import database_problems, demo_seeded, environment_problems

    problems = environment_problems()
    if problems:
        return _unavailable("; ".join(problems))
    if st.session_state.get(_GATE_KEY):
        return True

    from core.auth import _v2_session_scope
    from core.memory import load_memory

    try:
        with _v2_session_scope() as db_session:
            db_problems = database_problems(db_session, dict(load_memory().get("company") or {}))
            seeded = demo_seeded(db_session)
    except Exception as error:  # noqa: BLE001 - a broken database must fail closed, not crash
        return _unavailable(f"database check failed: {type(error).__name__}")
    if db_problems:
        return _unavailable("; ".join(db_problems))
    if not seeded:
        st.title("LeadLens CareOS — Public Demo")
        st.info("The demo is being prepared. Please check back shortly.")
        _LOG.error("Public demo not seeded yet: run scripts/seed_demo.py")
        return False
    st.session_state[_GATE_KEY] = True
    return True


def _enter_demo() -> None:
    from core.auth import _v2_session_scope
    from core.identity.session import store_session

    try:
        with _v2_session_scope() as db_session:
            session = build_demo_session(db_session)
    except DemoNotReady:
        st.info("The demo is being prepared. Please check back shortly.")
        return
    store_session(session)
    st.rerun()


def require_demo_session() -> bool:
    """Returns True once the visitor is inside the demo workspace; renders
    the automatic admission (or an unavailable notice) and returns False
    otherwise."""
    from core.auth import _restore_session_from_reload_token, current_authenticated_session
    from core.identity.session import clear_session

    if not _safety_gate():
        return False

    session = current_authenticated_session()
    if session is not None and not is_demo_session(session):
        clear_session()  # a non-demo session can never exist here; never honor one
        session = None
    if session is not None:
        return True

    token = st.query_params.get("_auth", "")
    if token and _restore_session_from_reload_token(token):
        restored = current_authenticated_session()
        if is_demo_session(restored):
            return True
        clear_session()

    _enter_demo()  # no landing page or login: admit the visitor as the restricted demo viewer
    return False


def render_demo_banner() -> None:
    st.markdown(
        "<div style='background:#1b2a4a;color:#fff;padding:.55rem 1rem;border-radius:.5rem;"
        "margin:.25rem 0 .75rem;font-size:.95rem;text-align:center'>"
        f"<strong>{BANNER_TEXT}</strong> &nbsp;·&nbsp; Read-only. Nothing is sent, saved, or exported."
        "</div>",
        unsafe_allow_html=True,
    )
