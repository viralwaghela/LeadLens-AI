"""Login gate for the whole app.

Two login paths coexist behind a migration flag:

    LEADLENS_V2_AUTH_ENABLED unset/false (default)
        The historical shared-password gate — unchanged from before
        Phase 7. Shared User ID + password pairs per deployed instance
        (`APP_PASSWORD`/`APP_PASSWORD_RECEPTIONIST`), not real per-user
        accounts. See the "legacy" section below for the original
        design notes.

    LEADLENS_V2_AUTH_ENABLED=true
        Phase 7's real identity login: email + password against
        core/identity/'s User/Membership/Organization tables via
        core.identity.authentication_service.authenticate(), Argon2id
        password hashing, per-organization role/permissions. The
        legacy shared-password path is completely bypassed when this
        is on — there is never a session where both login identities
        are simultaneously valid (see docs/V2_PHASE7_AUTH_CUTOVER.md).

Rollback: set LEADLENS_V2_AUTH_ENABLED back to false (or unset it).
No data is deleted by either direction — Phase 1's identity tables and
the legacy env-var credentials both persist regardless of which path is
currently active.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
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


def v2_auth_enabled() -> bool:
    return os.getenv("LEADLENS_V2_AUTH_ENABLED", "").strip().lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Legacy shared-password path (unchanged behavior — active only when
# v2_auth_enabled() is False)
# ---------------------------------------------------------------------------

def _configured_password() -> str:
    return os.getenv("APP_PASSWORD", "").strip()


def _configured_receptionist_password() -> str:
    if not _configured_password():
        return ""
    return os.getenv("APP_PASSWORD_RECEPTIONIST", "").strip()


def _configured_user_id() -> str:
    return os.getenv("APP_USER_ID", "").strip() or "owner"


def _configured_receptionist_user_id() -> str:
    return os.getenv("APP_USER_ID_RECEPTIONIST", "").strip() or "receptionist"


def current_role() -> str:
    """The legacy access level (ROLE_FULL / ROLE_CRM_ONLY) of the
    currently logged-in legacy session. Only meaningful when
    v2_auth_enabled() is False and require_login() has returned True —
    see current_authenticated_session() for the V2 equivalent."""
    return st.session_state.get(ROLE_KEY, ROLE_FULL)


def _sign(payload: str) -> str:
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
    returned True this session. Dispatches to the V2 form when V2 auth is
    enabled — see _v2_reload_token()."""
    if v2_auth_enabled():
        return _v2_reload_token()
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


def _require_login_legacy() -> bool:
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

    user_id = _configured_user_id()
    receptionist_user_id = _configured_receptionist_user_id()

    st.markdown(
        "<div style='max-width:420px;margin:8rem auto 0;text-align:center'>"
        "<h2>🔒 LeadLens CareOS</h2>"
        "<p style='opacity:.7'>Enter your User ID and password to continue.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.4, 1])
    with center:
        with st.form("login_form"):
            entered_id = st.text_input("User ID", placeholder="User ID")
            entered_password = st.text_input("Password", type="password", placeholder="Password")
            submitted = st.form_submit_button("Unlock", use_container_width=True, type="primary")
        if submitted:
            entered_id_clean = entered_id.strip().lower()
            entered_password_clean = entered_password.strip()
            if entered_id_clean == user_id.lower() and hmac.compare_digest(
                entered_password_clean, password
            ):
                st.session_state[SESSION_KEY] = True
                st.session_state[ROLE_KEY] = ROLE_FULL
                st.rerun()
            elif (
                receptionist_password
                and entered_id_clean == receptionist_user_id.lower()
                and hmac.compare_digest(entered_password_clean, receptionist_password)
            ):
                st.session_state[SESSION_KEY] = True
                st.session_state[ROLE_KEY] = ROLE_CRM_ONLY
                st.rerun()
            else:
                st.error("Incorrect User ID or password.")
    return False


def _render_logout_control_legacy() -> None:
    if not _configured_password():
        return
    if st.session_state.get(SESSION_KEY) and st.button("Log out", key="app_logout_btn", use_container_width=True):
        st.session_state[SESSION_KEY] = False
        st.session_state.pop(ROLE_KEY, None)
        st.rerun()


# ---------------------------------------------------------------------------
# Phase 7 — V2 real-identity login (active only when v2_auth_enabled())
# ---------------------------------------------------------------------------

_V2_ENGINE = None

_FAILED_ATTEMPTS_KEY = "v2_auth_failed_attempts"
_LOCKOUT_UNTIL_KEY = "v2_auth_lockout_until"
_PENDING_USER_KEY = "v2_auth_pending_user_id"
_PENDING_ORGS_KEY = "v2_auth_pending_memberships"

_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 30

# Reload-token signing key for V2 sessions — deliberately NOT derived from
# APP_PASSWORD (a rollback-mode secret with a different purpose). Falls
# back to a process-local random key generated once at import time if
# LEADLENS_V2_AUTH_SESSION_SECRET isn't set: an outstanding 20-second
# reload token becoming invalid across a process restart just forces one
# extra, harmless DB-validated re-login — never a security regression,
# since the reload token is only ever a continuity convenience, not the
# actual authorization boundary (current_authenticated_session() always
# re-validates against the database regardless of how a session started).
_V2_SESSION_SECRET = os.getenv("LEADLENS_V2_AUTH_SESSION_SECRET", "").strip() or secrets.token_hex(32)


def _v2_engine():
    global _V2_ENGINE
    if _V2_ENGINE is None:
        from core.db.session import make_engine

        _V2_ENGINE = make_engine()
    return _V2_ENGINE


def _v2_sign(payload: str) -> str:
    return hmac.new(_V2_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def _v2_reload_token() -> str:
    """Short-lived, signed continuity token carrying just enough to
    identify WHO to re-validate on the other side of a forced browser
    navigation — never trusted on its own for authorization. The
    restoring side (_require_login_v2()) always re-runs
    current_authenticated_session()'s full database revalidation
    immediately after restoring from this token, so a stale or even
    maliciously-crafted-but-correctly-signed token can never grant more
    than "try to re-validate this user in this organization" — it
    cannot elevate role, switch organization, or skip revalidation."""
    from core.identity.session import get_stored_session

    stored = get_stored_session()
    if stored is None:
        return ""
    # Phase 7.1.1: full sub-second precision (not int(time.time())) — an
    # integer-truncated timestamp compared against a full-precision
    # logout_epoch() could wrongly reject a token minted in the same
    # wall-clock second as a *prior* logout, even though the mint
    # genuinely happened after it. repr() round-trips exactly via
    # float() below, unlike str() for some platforms/values.
    ts = repr(time.time())
    payload = f"{ts}:{stored.user_id}:{stored.organization_id}"
    return f"{payload}.{_v2_sign(payload)}"


def _v2_reload_token_identity(token: str) -> tuple[int, int] | None:
    try:
        payload, sig = token.rsplit(".", 1)
        ts_text, user_id_text, org_id_text = payload.split(":", 2)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _v2_sign(payload)):
        return None
    try:
        # float() parses both this version's full-precision timestamps
        # and any pre-Phase-7.1.1 integer-string ones the same way —
        # no format flag or migration needed for a ~20-second-TTL token.
        issued_at = float(ts_text)
        age = time.time() - issued_at
        user_id, organization_id = int(user_id_text), int(org_id_text)
    except ValueError:
        return None
    if not (0 <= age < _RELOAD_TOKEN_TTL_SECONDS):
        return None
    # Phase 7.1: a token minted before this browser tab's last logout must
    # never restore a session, even if it's still within its own TTL — see
    # core/identity/session.py's clear_session()/logout_epoch().
    from core.identity.session import logout_epoch

    epoch = logout_epoch()
    if epoch is not None and issued_at <= epoch:
        return None
    return user_id, organization_id


def current_authenticated_session():
    """The canonical, revalidating accessor for the current V2 session.
    Returns None if there is no session, or if the underlying
    user/membership/organization is no longer valid (disabled, etc.) —
    in which case the stale stored session is cleared as a side effect,
    so a subsequent render shows the login screen rather than silently
    retrying. Re-derives role/permissions fresh from the database every
    call (Phase 1's resolve_identity() chain), so a role changed or a
    membership disabled after login stops granting access within one
    call of this function — see docs/V2_PHASE7_AUTH_CUTOVER.md for the
    practical validation cadence this implies for Streamlit's rerun
    model. Used by both this module and services/authorization_guard.py."""
    from core.db.session import session_scope
    from core.identity.session import AuthenticatedSession, clear_session, get_stored_session, store_session

    stored = get_stored_session()
    if stored is None:
        return None
    try:
        with session_scope(_v2_engine()) as db_session:
            from core.identity.tenant_context import build_user_context

            context = build_user_context(
                db_session, user_id=stored.user_id, organization_id=stored.organization_id,
                source="streamlit_v2_session",
            )
            if context is None:
                clear_session()
                return None
            refreshed = AuthenticatedSession(
                user_id=context.user_id,
                email=stored.email,
                organization_id=context.organization_id,
                organization_name=stored.organization_name,
                membership_id=context.membership_id,
                role=context.role,
                permissions=context.permissions,
            )
    except Exception:  # noqa: BLE001 - DB unavailable must fail closed, not crash the app
        clear_session()
        return None
    if refreshed != stored:
        store_session(refreshed)
    return refreshed


def _check_rate_limit() -> str | None:
    lockout_until = st.session_state.get(_LOCKOUT_UNTIL_KEY, 0)
    if time.time() < lockout_until:
        remaining = int(lockout_until - time.time()) + 1
        return f"Too many failed attempts. Try again in {remaining}s."
    return None


def _record_failed_attempt() -> None:
    attempts = st.session_state.get(_FAILED_ATTEMPTS_KEY, 0) + 1
    st.session_state[_FAILED_ATTEMPTS_KEY] = attempts
    if attempts >= _MAX_FAILED_ATTEMPTS:
        st.session_state[_LOCKOUT_UNTIL_KEY] = time.time() + _LOCKOUT_SECONDS
        st.session_state[_FAILED_ATTEMPTS_KEY] = 0


def _reset_failed_attempts() -> None:
    st.session_state.pop(_FAILED_ATTEMPTS_KEY, None)
    st.session_state.pop(_LOCKOUT_UNTIL_KEY, None)


def _clear_pending_membership_choice() -> None:
    st.session_state.pop(_PENDING_USER_KEY, None)
    st.session_state.pop(_PENDING_ORGS_KEY, None)


def _complete_login(db_session, user, membership) -> None:
    from core.identity.organization_service import get_organization
    from core.identity.permissions import permissions_for_role
    from core.identity.session import AuthenticatedSession, store_session

    org = get_organization(db_session, membership.organization_id)
    store_session(
        AuthenticatedSession(
            user_id=user.id,
            email=user.email,
            organization_id=membership.organization_id,
            organization_name=org.name if org else "",
            membership_id=membership.id,
            role=membership.role,
            permissions=permissions_for_role(membership.role),
        )
    )
    _clear_pending_membership_choice()


def _render_v2_login_form() -> None:
    """Renders the email/password form. Never called while a multi-
    membership organization choice is pending — _require_login_v2()
    routes to _render_organization_picker() first in that case."""
    from core.identity.authentication_service import authenticate
    from services.security_service import audit_event

    st.markdown(
        "<div style='max-width:420px;margin:8rem auto 0;text-align:center'>"
        "<h2>🔒 LeadLens CareOS</h2>"
        "<p style='opacity:.7'>Sign in with your email and password.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.4, 1])

    lockout_message = _check_rate_limit()
    with center:
        with st.form("v2_login_form"):
            email = st.text_input("Email", placeholder="you@clinic.example")
            password = st.text_input("Password", type="password", placeholder="Password")
            submitted = st.form_submit_button(
                "Sign in", use_container_width=True, type="primary", disabled=bool(lockout_message),
            )
        if lockout_message:
            st.error(lockout_message)
        if submitted and not lockout_message:
            with _v2_session_scope() as db_session:
                result = authenticate(db_session, email=email.strip(), password=password)
                if not result.success or not result.memberships:
                    _record_failed_attempt()
                    audit_event(
                        "unknown", "login_failed", "user",
                        "invalid credentials or no active organization access",
                    )
                    st.error("Invalid email or password.")
                    return
                _reset_failed_attempts()
                if len(result.memberships) == 1:
                    _complete_login(db_session, result.user, result.memberships[0])
                    audit_event(str(result.user.email), "login_success", "user", "")
                    st.rerun()
                else:
                    st.session_state[_PENDING_USER_KEY] = result.user.id
                    st.session_state[_PENDING_ORGS_KEY] = [m.id for m in result.memberships]
                    st.rerun()


def _v2_session_scope():
    from core.db.session import session_scope

    return session_scope(_v2_engine())


def _render_organization_picker() -> None:
    """Rendered only after a successful password check when the user
    belongs to more than one organization. Choices are restricted to
    exactly the Membership rows authenticate() already returned as
    ACTIVE for this user — never an arbitrary organization id, and never
    re-checking the password (already verified this login attempt)."""
    from core.identity.membership_service import get_membership
    from core.identity.organization_service import get_organization

    pending_user_id = st.session_state.get(_PENDING_USER_KEY)
    pending_membership_ids = st.session_state.get(_PENDING_ORGS_KEY, [])
    if not pending_user_id or not pending_membership_ids:
        return

    st.markdown(
        "<div style='max-width:420px;margin:8rem auto 0;text-align:center'>"
        "<h2>Choose an organization</h2>"
        "<p style='opacity:.7'>Your account has access to more than one.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.4, 1])
    with center, _v2_session_scope() as db_session:
        options: dict[str, int] = {}
        for membership_id in pending_membership_ids:
            membership = get_membership(db_session, membership_id)
            if membership is None or membership.user_id != pending_user_id:
                continue  # ignore anything that doesn't match the authenticated user
            org = get_organization(db_session, membership.organization_id)
            if org is None:
                continue
            options[f"{org.name} — {membership.role.value.title()}"] = membership_id

        if not options:
            st.error("No valid organization access found for this account.")
            _clear_pending_membership_choice()
            return

        with st.form("v2_org_picker_form"):
            choice = st.radio("Organization", list(options.keys()), label_visibility="collapsed")
            confirmed = st.form_submit_button("Continue", use_container_width=True, type="primary")
        if confirmed:
            chosen_membership_id = options[choice]
            membership = get_membership(db_session, chosen_membership_id)
            if membership is None or membership.user_id != pending_user_id:
                st.error("Selection is no longer valid — please sign in again.")
                _clear_pending_membership_choice()
                return
            from core.identity.user_service import get_user

            user = get_user(db_session, pending_user_id)
            if user is None:
                _clear_pending_membership_choice()
                st.error("Selection is no longer valid — please sign in again.")
                return
            _complete_login(db_session, user, membership)
            from services.security_service import audit_event

            audit_event(str(user.email), "login_success", "user", f"organization={membership.organization_id}")
            st.rerun()


def _restore_session_from_reload_token(token: str) -> bool:
    """Resolves the (user_id, organization_id) pair a signed reload
    token identifies directly through build_user_context() — the exact
    same full database revalidation current_authenticated_session()
    itself uses — rather than storing an unvalidated placeholder session
    first. A forged, expired, or stale-but-correctly-signed token can
    therefore never grant anything beyond "attempt one fresh, real
    authorization check for this user in this organization." Returns
    True and stores a fully-validated session on success."""
    identity = _v2_reload_token_identity(token)
    if identity is None:
        return False
    user_id, organization_id = identity
    from core.identity.organization_service import get_organization
    from core.identity.session import AuthenticatedSession, store_session
    from core.identity.tenant_context import build_user_context
    from core.identity.user_service import get_user

    with _v2_session_scope() as db_session:
        context = build_user_context(
            db_session, user_id=user_id, organization_id=organization_id, source="reload_token",
        )
        if context is None:
            return False
        user = get_user(db_session, user_id)
        org = get_organization(db_session, organization_id)
        store_session(
            AuthenticatedSession(
                user_id=context.user_id,
                email=user.email if user else "",
                organization_id=context.organization_id,
                organization_name=org.name if org else "",
                membership_id=context.membership_id,
                role=context.role,
                permissions=context.permissions,
            )
        )
    return True


def _require_login_v2() -> bool:
    session = current_authenticated_session()
    if session is not None:
        return True

    token = st.query_params.get("_auth", "")
    if token and _restore_session_from_reload_token(token):
        return True

    if st.session_state.get(_PENDING_USER_KEY):
        _render_organization_picker()
        return False

    _render_v2_login_form()
    return False


def _render_logout_control_v2() -> None:
    from core.identity.session import clear_session, get_stored_session

    if get_stored_session() is None:
        return
    if st.button("Log out", key="app_logout_btn_v2", use_container_width=True):
        from services.security_service import audit_event

        stored = get_stored_session()
        clear_session()
        # Phase 7.1: an _auth reload token embedded in this tab's URL (per
        # workspace_theme.py's forced-navigation mechanism) is not a real
        # browser navigation away from — st.rerun() leaves st.query_params
        # untouched, so without this the very next rerun would silently
        # restore the just-cleared session via _require_login_v2()'s
        # token-restore path. Defense in depth alongside logout_epoch().
        if "_auth" in st.query_params:
            del st.query_params["_auth"]
        if stored is not None:
            audit_event(stored.email, "logout", "user", "")
        st.rerun()


# ---------------------------------------------------------------------------
# Public dispatch — used by app.py / dashboard.py
# ---------------------------------------------------------------------------

def require_login() -> bool:
    """Render a login screen if needed. Returns True once access is allowed.

    Call this at the very top of app.py, before any business data is loaded
    or rendered, and stop execution (return) if it returns False.
    """
    if v2_auth_enabled():
        return _require_login_v2()
    return _require_login_legacy()


def render_logout_control() -> None:
    """Optional small logout button — call from the sidebar once logged in."""
    if v2_auth_enabled():
        _render_logout_control_v2()
    else:
        _render_logout_control_legacy()
