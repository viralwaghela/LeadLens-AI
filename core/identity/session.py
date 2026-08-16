"""Phase 7 — the authenticated Streamlit session representation.

Deliberately small: only the safe identity metadata core/auth.py's V2
login path needs to carry across Streamlit reruns
(`st.session_state` survives reruns within one browser tab, but never a
real browser navigation — see core/auth.py's reload-token machinery for
that separate problem). Never stores a password, a password hash, or a
decrypted integration secret.

`AuthenticatedSession` is intentionally NOT trusted as a permanent
source of truth: `revalidate()` re-derives everything from the database
via the same trusted core.identity.authorization_service.resolve_identity()
path Phase 1 already built and tested, so a user/membership/organization
disabled *after* login stops being effective within one revalidation
cycle rather than indefinitely (see core/auth.py's V2 login path for
when revalidation is actually called).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import streamlit as st

from core.db.models.identity import MembershipRole
from core.identity.permissions import permissions_for_role

_SESSION_KEY = "v2_auth_session"

# Phase 7.1 — deliberately NOT one of clear_session()'s own stale_prefixes
# (v2_auth_/crm_/jarvis_/workspace_), so logout doesn't wipe its own marker.
# Records when this browser tab last logged out, so a reload token minted
# before that moment (e.g. still sitting in the URL from a workspace-theme
# reload a moment earlier, per core/auth.py's _v2_reload_token_identity())
# can be rejected even though it's within its own TTL — see
# docs/V2_PHASE7_AUTH_CUTOVER.md's Phase 7.1 addendum.
_LOGOUT_EPOCH_KEY = "_session_logout_epoch"


@dataclass(frozen=True)
class AuthenticatedSession:
    user_id: int
    email: str
    organization_id: int
    organization_name: str
    membership_id: int
    role: MembershipRole
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def to_state_dict(self) -> dict:
        """Plain-value form for st.session_state (dataclasses with enum
        members round-trip fine, but this keeps the stored shape
        explicit and easy to audit for "does this ever hold a secret" —
        it never does)."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "organization_id": self.organization_id,
            "organization_name": self.organization_name,
            "membership_id": self.membership_id,
            "role": self.role.value,
            "permissions": sorted(self.permissions),
        }

    @classmethod
    def from_state_dict(cls, data: dict) -> "AuthenticatedSession":
        return cls(
            user_id=int(data["user_id"]),
            email=str(data["email"]),
            organization_id=int(data["organization_id"]),
            organization_name=str(data["organization_name"]),
            membership_id=int(data["membership_id"]),
            role=MembershipRole(data["role"]),
            permissions=frozenset(data.get("permissions", [])),
        )


def store_session(session: AuthenticatedSession) -> None:
    st.session_state[_SESSION_KEY] = session.to_state_dict()


def get_stored_session() -> AuthenticatedSession | None:
    """Returns the session as last stored — NOT re-validated against the
    database. Callers that need the current-as-of-right-now truth
    (anything gating an action) should go through
    core.auth.current_authenticated_session(), which revalidates first.
    Safe to call from any context, including outside a real Streamlit
    run (e.g. a script or test that never populated session_state) —
    returns None rather than raising."""
    try:
        data = st.session_state.get(_SESSION_KEY)
    except Exception:  # noqa: BLE001 - no live Streamlit session/runtime
        return None
    if not data:
        return None
    try:
        return AuthenticatedSession.from_state_dict(data)
    except (KeyError, ValueError):
        return None


def resolve_authenticated_tenant_context(db_session):
    """Phase 7 — the live-app source of TenantContext once V2 auth is
    enabled and a human is logged in: authenticated user + validated
    Membership + Organization, never the transitional default resolver,
    never a client-supplied organization_id. Re-derives fully from the
    database on every call via the same trusted
    core.identity.authorization_service.resolve_identity() path
    build_user_context() already uses — never trusts the stored session
    dict's fields for authorization, only for "which user/org to look
    up." Returns None if no session is stored, or if that user's access
    is no longer valid (disabled user/membership/organization) — callers
    must treat None as "not authorized," never fall back to any other
    organization."""
    from core.identity.tenant_context import build_user_context

    stored = get_stored_session()
    if stored is None:
        return None
    return build_user_context(
        db_session, user_id=stored.user_id, organization_id=stored.organization_id,
        source="streamlit_v2_session",
    )


def clear_session() -> None:
    """Logout: clears the authenticated identity and every other
    tenant-specific key a prior session might have left behind, so nothing
    from user A's session is visible to user B logging in next in the same
    browser tab. Streamlit's own st.session_state is per-browser-session
    already (not shared across users), but this is defensive: it also
    clears CRM/Jarvis UI state (selected patient, workspace page, etc.)
    that could otherwise display stale organization-A content for one
    render before the next login's own data loads."""
    stale_prefixes = ("v2_auth_", "crm_", "jarvis_", "workspace_")
    for key in list(st.session_state.keys()):
        if key == _SESSION_KEY or any(key.startswith(p) for p in stale_prefixes):
            del st.session_state[key]
    import time

    st.session_state[_LOGOUT_EPOCH_KEY] = time.time()


def logout_epoch() -> float | None:
    """The time.time() this browser tab last logged out, or None if it
    never has (within this session_state's lifetime). Used by
    core.auth._v2_reload_token_identity() to reject a reload token minted
    before this moment, even if the token itself is still within its own
    TTL — see clear_session()."""
    try:
        value = st.session_state.get(_LOGOUT_EPOCH_KEY)
    except Exception:  # noqa: BLE001 - no live Streamlit session/runtime
        return None
    return float(value) if value is not None else None
