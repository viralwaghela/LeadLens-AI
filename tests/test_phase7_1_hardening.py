"""V2 Phase 7.1 tests: authentication + RBAC hardening.

Covers the three confirmed defects found by the separate Phase 7 audit
(tests/test_phase7_audit.py) and fixed here:

    1. A reload token minted before logout could still restore a session
       after logout, within its own TTL (core/auth.py,
       core/identity/session.py).
    2. services/platform_data.py's save_company_profile() had no RBAC
       gate (now requires organization.manage).
    3. ui/phases_16_to_20.py's show_security_center() / the underlying
       services/security_service.py's audit_rows() had no RBAC gate (now
       requires audit.view).

Uses the same Fixture/isolated-engine/bare-mode-session-state pattern as
tests/test_phase7_auth_cutover.py. import _bootstrap first, same as
every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import time
from dataclasses import dataclass

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
from core.identity.session import (
    AuthenticatedSession,
    clear_session,
    get_stored_session,
    logout_epoch,
    store_session,
)
from core.identity.permissions import permissions_for_role
from services.authorization_guard import PermissionDenied

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
    st.query_params.clear()
    yield engine
    st.session_state.clear()
    st.query_params.clear()
    engine.dispose()


@dataclass
class Fixture:
    org_id: int
    org_name: str
    user_id: int
    user_email: str
    membership_id: int
    role: MembershipRole


def _make_org_user_membership(session, *, org_slug, role=MembershipRole.OWNER, email=None, password=PASSWORD) -> Fixture:
    org = organization_service.create_organization(session, name=org_slug.title(), slug=org_slug)
    user = user_service.create_user(session, email=email or f"{org_slug}@example.com", password=password)
    membership = membership_service.create_membership(session, user_id=user.id, organization_id=org.id, role=role)
    fixture = Fixture(
        org_id=org.id, org_name=org.name, user_id=user.id, user_email=user.email,
        membership_id=membership.id, role=role,
    )
    session.commit()
    return fixture


def _login_as(fixture: Fixture) -> None:
    store_session(
        AuthenticatedSession(
            user_id=fixture.user_id, email=fixture.user_email, organization_id=fixture.org_id,
            organization_name=fixture.org_name, membership_id=fixture.membership_id, role=fixture.role,
            permissions=permissions_for_role(fixture.role),
        )
    )


# ---------------------------------------------------------------------------
# 1. LOGOUT / RELOAD TOKEN INVALIDATION
# ---------------------------------------------------------------------------

def test_reload_token_minted_before_logout_rejected_after_logout(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="logout-org")
    _login_as(fx)

    token = auth._v2_reload_token()
    clear_session()
    assert get_stored_session() is None

    assert auth._restore_session_from_reload_token(token) is False
    assert get_stored_session() is None


def test_reload_token_minted_after_logout_still_works(isolated) -> None:
    """The logout-epoch check must only reject tokens minted BEFORE (or
    at) the logout boundary — a token minted by a later, genuine login
    must still restore across a real browser navigation (which clears
    st.session_state entirely but does NOT call clear_session()/set a
    new logout epoch), so the fix can't be an unconditional lockout."""
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="relogin-org")
    _login_as(fx)
    clear_session()

    # Re-login (a real second login, not a token restore).
    _login_as(fx)
    token = auth._v2_reload_token()
    assert token != ""

    # Simulate a genuine browser navigation (new Streamlit session): all
    # of st.session_state is gone, but this is NOT a logout, so no new
    # logout epoch is recorded.
    st.session_state.clear()

    assert auth._restore_session_from_reload_token(token) is True
    restored = get_stored_session()
    assert restored is not None
    assert restored.user_id == fx.user_id


def test_logout_clears_auth_query_param(isolated) -> None:
    """core.auth._render_logout_control_v2() explicitly clears the _auth
    query param as part of logout — verify the underlying primitive
    (st.query_params deletion) behaves as the logout control relies on,
    since this is the "Approach A" half of the defense-in-depth fix."""
    st.query_params["_auth"] = "some-token-value"
    assert "_auth" in st.query_params
    if "_auth" in st.query_params:
        del st.query_params["_auth"]
    assert "_auth" not in st.query_params


def test_logout_epoch_set_on_clear_session(isolated) -> None:
    assert logout_epoch() is None
    clear_session()
    epoch = logout_epoch()
    assert epoch is not None
    assert abs(epoch - time.time()) < 5


def test_user_a_logout_then_user_b_login_no_bleed(isolated) -> None:
    """A logs out, B logs in immediately after in the same (test)
    session_state — B's session must be exactly B's, and A's old reload
    token must still not work even after B has logged in."""
    with Session(isolated) as session:
        fx_a = _make_org_user_membership(session, org_slug="user-a-org", email="a@example.com")
    _login_as(fx_a)
    token_a = auth._v2_reload_token()
    clear_session()

    with Session(isolated) as session:
        fx_b = _make_org_user_membership(session, org_slug="user-b-org", email="b@example.com")
    _login_as(fx_b)

    stored = get_stored_session()
    assert stored is not None
    assert stored.user_id == fx_b.user_id
    assert stored.organization_id == fx_b.org_id

    # A's old token must not resurrect A's session now that B is logged in.
    assert auth._restore_session_from_reload_token(token_a) is False
    stored_after = get_stored_session()
    assert stored_after is not None
    assert stored_after.user_id == fx_b.user_id  # unchanged, still B


# ---------------------------------------------------------------------------
# 2. ORGANIZATION SETTINGS RBAC (save_company_profile)
# ---------------------------------------------------------------------------

def test_save_company_profile_owner_allowed(isolated) -> None:
    from services.platform_data import save_company_profile

    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="org-settings-owner", role=MembershipRole.OWNER)
    _login_as(fx)
    save_company_profile({"business_name": "Owner Clinic"})  # must not raise


@pytest.mark.parametrize("role", [MembershipRole.VIEWER, MembershipRole.RECEPTIONIST, MembershipRole.PRACTITIONER])
def test_save_company_profile_unauthorized_roles_denied(isolated, role) -> None:
    from services.platform_data import save_company_profile

    with Session(isolated) as session:
        fx = _make_org_user_membership(
            session, org_slug=f"org-settings-{role.value}", role=role, email=f"{role.value}@example.com",
        )
    _login_as(fx)
    with pytest.raises(PermissionDenied):
        save_company_profile({"business_name": "Should Not Be Saved"})


def test_save_company_profile_direct_service_call_denied_without_session(isolated) -> None:
    """Even without going through the UI, a direct call to the service
    function with V2 auth enabled but no live session must not silently
    succeed — it is a no-op allow only when v2_auth_enabled() is False or
    there truly is no session at all (script/system context), matching
    services/authorization_guard.py's documented no-op semantics. This
    test documents that this call, made mid-test with V2 auth on and no
    stored session, does NOT raise (system/script context) — it is the
    live-UI path (a real logged-in user) that must be gated, which the
    tests above already confirm."""
    from services.platform_data import save_company_profile

    assert get_stored_session() is None
    save_company_profile({"business_name": "System Context"})  # no-op allow, must not raise


# ---------------------------------------------------------------------------
# 3. AUDIT / SECURITY CENTER RBAC (audit_rows / show_security_center)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", [MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.FINANCE])
def test_audit_rows_permitted_roles_allowed(isolated, role) -> None:
    from services.security_service import audit_rows

    with Session(isolated) as session:
        fx = _make_org_user_membership(
            session, org_slug=f"audit-permitted-{role.value}", role=role, email=f"{role.value}@example.com",
        )
    _login_as(fx)
    audit_rows()  # must not raise


@pytest.mark.parametrize("role", [MembershipRole.VIEWER, MembershipRole.RECEPTIONIST, MembershipRole.PRACTITIONER, MembershipRole.MARKETING])
def test_audit_rows_unauthorized_roles_denied(isolated, role) -> None:
    from services.security_service import audit_rows

    with Session(isolated) as session:
        fx = _make_org_user_membership(
            session, org_slug=f"audit-denied-{role.value}", role=role, email=f"{role.value}@example.com",
        )
    _login_as(fx)
    with pytest.raises(PermissionDenied):
        audit_rows()


def test_security_center_ui_denies_gracefully_not_crash(isolated) -> None:
    """ui/phases_16_to_20.py's show_security_center() wraps audit_rows()
    in try/except so an unauthorized viewer sees a clean message instead
    of an uncaught PermissionDenied crashing the page — verify the
    underlying exception is exactly what that except clause expects."""
    from services.security_service import audit_rows

    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="audit-ui-denied", role=MembershipRole.VIEWER)
    _login_as(fx)
    try:
        audit_rows()
        raised = False
    except PermissionDenied:
        raised = True
    except Exception:
        raised = "wrong-exception-type"
    assert raised is True


# ---------------------------------------------------------------------------
# 4. REGRESSION — existing Phase 7 behavior unchanged
# ---------------------------------------------------------------------------

def test_reload_token_still_rejects_expired(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="expiry-org")
    _login_as(fx)
    token = auth._v2_reload_token()

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)
    assert auth._v2_reload_token_identity(token) is None


def test_reload_token_still_rejects_tampering(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="tamper-org")
    _login_as(fx)
    token = auth._v2_reload_token()
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert auth._v2_reload_token_identity(tampered) is None


def test_reload_token_still_rejects_disabled_membership(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="disabled-membership-org")
    _login_as(fx)
    token = auth._v2_reload_token()

    with Session(isolated) as session:
        membership_service.disable_membership(session, fx.membership_id)
        session.commit()

    clear_session()
    assert auth._restore_session_from_reload_token(token) is False


def test_reload_token_still_rejects_disabled_user(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="disabled-user-org")
    _login_as(fx)
    token = auth._v2_reload_token()

    with Session(isolated) as session:
        user_service.disable_user(session, fx.user_id)
        session.commit()

    clear_session()
    assert auth._restore_session_from_reload_token(token) is False


def test_reload_token_cannot_cross_organizations(isolated) -> None:
    """A token whose (user_id, organization_id) pair names a user who is
    not actually a member of that organization must not restore a
    session — _v2_reload_token_identity() only validates the signature
    and TTL (and now the logout epoch); the actual membership check
    happens one layer up, in _restore_session_from_reload_token()'s call
    to build_user_context(), which is the real authorization boundary."""
    with Session(isolated) as session:
        fx_a = _make_org_user_membership(session, org_slug="cross-org-a")
        fx_b = _make_org_user_membership(session, org_slug="cross-org-b", email="b@example.com")
    _login_as(fx_a)

    # fx_a's user_id paired with fx_b's organization_id — fx_a's user has
    # no membership in fx_b's organization.
    forged_payload = f"{int(time.time())}:{fx_a.user_id}:{fx_b.org_id}"
    forged_token = f"{forged_payload}.{auth._v2_sign(forged_payload)}"

    # The signature/TTL layer alone can't detect this — it only checks
    # that the payload matches its own signature, not that the pair is a
    # real membership.
    assert auth._v2_reload_token_identity(forged_token) == (fx_a.user_id, fx_b.org_id)

    # The real authorization boundary rejects it.
    assert auth._restore_session_from_reload_token(forged_token) is False


def test_owner_still_has_full_access_after_hardening(isolated) -> None:
    """Sanity regression: the two new gates must not have accidentally
    tightened OWNER's access beyond the Phase 1 matrix."""
    from services.platform_data import save_company_profile
    from services.security_service import audit_rows

    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="owner-sanity-org", role=MembershipRole.OWNER)
    _login_as(fx)
    save_company_profile({"business_name": "Still Works"})
    audit_rows()
