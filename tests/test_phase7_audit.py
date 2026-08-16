"""Independent adversarial audit tests for V2 Phase 7 (live auth cutover
+ RBAC). Written separately from tests/test_phase7_auth_cutover.py as a
second, independently-designed adversarial pass. import _bootstrap
first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import pytest
import streamlit as st
from sqlalchemy.orm import Session

import core.auth as auth
import core.memory as business_memory
import services.integration_credentials as ic
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole
from core.db.session import make_engine
from core.identity import membership_service, organization_service, user_service
from core.identity.session import AuthenticatedSession, clear_session, get_stored_session, store_session
from core.identity.permissions import permissions_for_role

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr(auth, "_V2_ENGINE", engine)
    monkeypatch.setenv("LEADLENS_V2_AUTH_ENABLED", "true")
    st.session_state.clear()
    yield engine
    st.session_state.clear()
    engine.dispose()


def _make_org_user_membership(session, *, org_slug, role=MembershipRole.OWNER, email=None, password=PASSWORD):
    org = organization_service.create_organization(session, name=org_slug.title(), slug=org_slug)
    user = user_service.create_user(session, email=email or f"{org_slug}@example.com", password=password)
    membership = membership_service.create_membership(session, user_id=user.id, organization_id=org.id, role=role)
    session.commit()
    return org.id, org.name, user.id, user.email, membership.id


def _login(org_id, org_name, user_id, email, membership_id, role) -> None:
    store_session(
        AuthenticatedSession(
            user_id=user_id, email=email, organization_id=org_id, organization_name=org_name,
            membership_id=membership_id, role=role, permissions=permissions_for_role(role),
        )
    )


# ---------------------------------------------------------------------------
# Section 20: reload-token reuse after logout
# ---------------------------------------------------------------------------

def test_reload_token_minted_before_logout_still_restores_session_after_logout(isolated) -> None:
    """Candidate finding: does logout invalidate an already-minted,
    still-unexpired reload token? clear_session() only touches
    st.session_state — it never revokes a token that was already handed
    to the browser (e.g. embedded in the URL by a workspace-theme
    reload a moment earlier, per ui/workspace_theme.py)."""
    with Session(isolated) as session:
        org_id, org_name, user_id, email, membership_id = _make_org_user_membership(session, org_slug="reuse-org")
    _login(org_id, org_name, user_id, email, membership_id, MembershipRole.OWNER)

    # A token minted while genuinely logged in (e.g. during a workspace
    # switch) — this is exactly what ends up sitting in the browser's
    # URL bar per ui/workspace_theme.py's meta-refresh mechanism.
    token = auth._v2_reload_token()

    # User logs out.
    clear_session()
    assert get_stored_session() is None

    # The token is still within its TTL (a workspace switch and a
    # logout both plausibly happen within the same 20 seconds). If the
    # old URL query string (containing this token) is still present on
    # the next rerun — which it is, since st.rerun() is not a real
    # browser navigation and never clears st.query_params — does the
    # app silently log the user back in?
    restored = auth._restore_session_from_reload_token(token)
    assert restored is False, (
        "A reload token minted before logout can still restore a session "
        "after logout, within its TTL window — logout does not fully "
        "invalidate session continuity."
    )


# ---------------------------------------------------------------------------
# Section 9 / 12: "organization settings" and "audit/security viewing" as
# named backend-authorization examples — are they actually gated?
# ---------------------------------------------------------------------------

def test_save_company_profile_requires_organization_manage(isolated) -> None:
    """Phase 7.1 fix verification: services/platform_data.py's
    save_company_profile() — the live write path behind the CRM
    "Settings > Clinic details" tab (ui/data_hub.py) — must now be gated
    by organization.manage (OWNER-only per the Phase 1 matrix). This
    replaces the Phase 7 audit's finding that it had no gate at all."""
    from services.authorization_guard import PermissionDenied
    from services.platform_data import save_company_profile

    with Session(isolated) as session:
        org_id, org_name, user_id, email, membership_id = _make_org_user_membership(
            session, org_slug="settings-owner-org", role=MembershipRole.OWNER,
        )
    _login(org_id, org_name, user_id, email, membership_id, MembershipRole.OWNER)
    save_company_profile({"business_name": "Owner Clinic"})  # must not raise

    clear_session()
    with Session(isolated) as session:
        org_id, org_name, user_id, email, membership_id = _make_org_user_membership(
            session, org_slug="settings-viewer-org", role=MembershipRole.VIEWER,
            email="viewer@example.com",
        )
    _login(org_id, org_name, user_id, email, membership_id, MembershipRole.VIEWER)
    with pytest.raises(PermissionDenied):
        save_company_profile({"business_name": "Viewer Should Not Be Able To Do This"})


def test_security_center_audit_log_view_requires_audit_view(isolated) -> None:
    """Phase 7.1 fix verification: services/security_service.py's
    audit_rows() — the only live caller of which is
    ui/phases_16_to_20.py's show_security_center() ("Settings > Data
    protection") — must now be gated by audit.view (OWNER/ADMIN/FINANCE
    per the Phase 1 matrix). This replaces the Phase 7 audit's finding
    that it had no gate at all."""
    from services.authorization_guard import PermissionDenied
    from services.security_service import audit_rows

    with Session(isolated) as session:
        org_id, org_name, user_id, email, membership_id = _make_org_user_membership(
            session, org_slug="audit-owner-org", role=MembershipRole.OWNER,
        )
    _login(org_id, org_name, user_id, email, membership_id, MembershipRole.OWNER)
    audit_rows()  # must not raise

    clear_session()
    with Session(isolated) as session:
        org_id, org_name, user_id, email, membership_id = _make_org_user_membership(
            session, org_slug="audit-receptionist-org", role=MembershipRole.RECEPTIONIST,
            email="receptionist@example.com",
        )
    _login(org_id, org_name, user_id, email, membership_id, MembershipRole.RECEPTIONIST)
    with pytest.raises(PermissionDenied):
        audit_rows()
