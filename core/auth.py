"""Password gate for the whole app, with two optional access levels.

Deliberately simple: shared passwords per deployed instance, kept in
`.env` / the host's secrets manager, not per-user accounts or real
multi-tenant login. `APP_PASSWORD` grants full access (CRM + JARVIS).
`APP_PASSWORD_RECEPTIONIST`, if also set, grants a second password that
only ever reaches the CRM workspace — dashboard.py forces workspace_mode
to CRM and hides the Core switch entirely for this role, at the routing
level, not just by hiding the button (see ROLE_CRM_ONLY usage there).

If APP_PASSWORD is left unset, the gate is skipped entirely (so local
development still works with zero setup) but a loud on-screen warning is
shown so an accidentally-unprotected deployment is hard to miss.
APP_PASSWORD_RECEPTIONIST only has any effect when APP_PASSWORD is also
set — a receptionist-only password with no full-access password
configured would be a confusing, half-protected deployment.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

import streamlit as st

SESSION_KEY = "app_authenticated"
ROLE_KEY = "app_role"
ROLE_FULL = "full"
ROLE_CRM_ONLY = "crm_only"

# How long a reload_token() stays valid, in seconds. Only needs to cover the
# round-trip of a single browser navigation (see workspace_theme.py's
# meta-refresh reload) — kept short so a copy-pasted URL containing a token
# can't be used to skip the password screen for long.
_RELOAD_TOKEN_TTL_SECONDS = 20


def _configured_password() -> str:
    return os.getenv("APP_PASSWORD", "").strip()


def _configured_receptionist_password() -> str:
    if not _configured_password():
        return ""
    return os.getenv("APP_PASSWORD_RECEPTIONIST", "").strip()


def current_role() -> str:
    """The access level of the currently logged-in session. Only meaningful
    after require_login() has returned True."""
    return st.session_state.get(ROLE_KEY, ROLE_FULL)


def _sign(payload: str) -> str:
    # Keyed on BOTH configured passwords together (not just APP_PASSWORD),
    # so a reload_token() minted for a receptionist session still verifies
    # correctly — verification doesn't need to know in advance which of the
    # two passwords this particular session logged in with.
    key = _configured_password() + "|" + _configured_receptionist_password()
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]


def reload_token() -> str:
    """A short-lived, signed proof that this session already passed
    require_login() as a given role, for carrying auth (and which access
    level it was) across the forced full-page reloads the Core switch's
    theme-forcing triggers (see workspace_theme.py) — those are genuine
    browser navigations, not Streamlit reruns, so st.session_state doesn't
    survive them and login would otherwise be required again on every
    workspace switch. Only call this after require_login() has already
    returned True this session.
    """
    role = current_role()
    ts = str(int(time.time()))
    payload = f"{ts}:{role}"
    return f"{payload}.{_sign(payload)}"


def _reload_token_role(token: str) -> str | None:
    try:
        payload, sig = token.rsplit(".", 1)
        ts_text, role = payload.split(":", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        age = time.time() - int(ts_text)
    except ValueError:
        return None
    if not (0 <= age < _RELOAD_TOKEN_TTL_SECONDS):
        return None
    if role not in (ROLE_FULL, ROLE_CRM_ONLY):
        return None
    return role


def require_login() -> bool:
    """Render a login screen if needed. Returns True once access is allowed.

    Call this at the very top of app.py, before any business data is loaded
    or rendered, and stop execution (return) if it returns False.
    """
    password = _configured_password()
    receptionist_password = _configured_receptionist_password()

    if not password:
        st.warning(
            "⚠️ No APP_PASSWORD is set — this dashboard is currently open to "
            "anyone with the URL. Set APP_PASSWORD in your .env file (or your "
            "hosting platform's secrets manager) before using this with real "
            "clinic data.",
            icon="⚠️",
        )
        st.session_state[ROLE_KEY] = ROLE_FULL
        return True

    if st.session_state.get(SESSION_KEY):
        return True

    token = st.query_params.get("_auth", "")
    if token:
        role = _reload_token_role(token)
        if role:
            st.session_state[SESSION_KEY] = True
            st.session_state[ROLE_KEY] = role
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
            entered_clean = entered.strip()
            if hmac.compare_digest(entered_clean, password):
                st.session_state[SESSION_KEY] = True
                st.session_state[ROLE_KEY] = ROLE_FULL
                st.rerun()
            elif receptionist_password and hmac.compare_digest(
                entered_clean, receptionist_password
            ):
                st.session_state[SESSION_KEY] = True
                st.session_state[ROLE_KEY] = ROLE_CRM_ONLY
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
        st.session_state.pop(ROLE_KEY, None)
        st.rerun()
