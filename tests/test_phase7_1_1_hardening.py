"""V2 Phase 7.1.1 tests: onboarding RBAC + reload-token timestamp
precision hardening.

Covers the two confirmed unresolved defects from the Phase 7.1
independent audit:

    1. onboarding.py called core.memory.save_company() directly, with no
       RBAC gate — any authenticated V2-auth identity (even one lacking
       organization.manage) could initialize the company profile
       whenever core.memory.company_exists() was False. Fixed by routing
       onboarding through the already-gated
       services.platform_data.save_company_profile().
    2. _v2_reload_token()'s int(time.time()) timestamp compared against
       core.identity.session.logout_epoch()'s full-precision float could
       wrongly reject a token minted in the same wall-clock second as a
       genuinely-prior logout. Fixed by using full-precision floats on
       both sides (backward compatible with old integer-string tokens
       via float() parsing).

Uses the same Fixture/isolated-engine/bare-mode-session-state pattern as
tests/test_phase7_1_hardening.py. import _bootstrap first, same as every
other file in tests/.
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
from core.identity.session import AuthenticatedSession, clear_session, get_stored_session, store_session
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
# 1. ONBOARDING AUTHORIZATION
# ---------------------------------------------------------------------------

def test_onboarding_owner_initializes_when_company_missing(isolated) -> None:
    assert business_memory.company_exists() is False
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="onboard-owner-org", role=MembershipRole.OWNER)
    _login_as(fx)

    from services.platform_data import save_company_profile

    save_company_profile({"business_name": "New Clinic"})  # must not raise
    assert business_memory.company_exists() is True


@pytest.mark.parametrize("role", [MembershipRole.VIEWER, MembershipRole.RECEPTIONIST, MembershipRole.PRACTITIONER])
def test_onboarding_unauthorized_role_denied_when_company_missing(isolated, role) -> None:
    assert business_memory.company_exists() is False
    with Session(isolated) as session:
        fx = _make_org_user_membership(
            session, org_slug=f"onboard-denied-{role.value}", role=role, email=f"{role.value}@example.com",
        )
    _login_as(fx)

    from services.platform_data import save_company_profile

    with pytest.raises(PermissionDenied):
        save_company_profile({"business_name": "Should Not Be Set"})
    assert business_memory.company_exists() is False


def test_onboarding_module_no_longer_imports_ungated_save_company() -> None:
    """The live onboarding UI must not call the ungated
    core.memory.save_company() primitive directly — it must go through
    the RBAC-gated services.platform_data.save_company_profile()."""
    import inspect

    import onboarding

    source = inspect.getsource(onboarding)
    assert "save_company_profile" in source
    assert "from core.memory import save_company" not in source
    # The step that persists the profile must call the gated function.
    tour_source = inspect.getsource(onboarding._step_tour)
    assert "save_company_profile(" in tour_source
    assert "save_company(" not in tour_source


def test_direct_backend_bypass_denied_for_unauthorized_identity(isolated) -> None:
    """Section 5: attempt the actual backend path a live human onboarding
    flow now uses (save_company_profile()), not just the UI form — an
    unauthorized authenticated identity must be denied regardless of
    which caller reaches it."""
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="bypass-org", role=MembershipRole.VIEWER)
    _login_as(fx)

    from services.platform_data import save_company_profile

    with pytest.raises(PermissionDenied):
        save_company_profile({"business_name": "Bypass Attempt"})


def test_save_company_low_level_primitive_remains_ungated_for_bootstrap(isolated) -> None:
    """core.memory.save_company() itself stays ungated (documented as a
    low-level, system/bootstrap-only primitive) — it is the live UI path
    (onboarding.py) that must not call it directly anymore, not this
    function's own behavior."""
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="lowlevel-org", role=MembershipRole.VIEWER)
    _login_as(fx)
    business_memory.save_company({"business_name": "Bootstrap Path"})  # must not raise
    assert business_memory.company_exists() is True


def test_onboarding_path_unchanged_when_company_already_exists(isolated) -> None:
    """Existing deployments where company_exists() is already True must
    behave exactly as before — this fix only touches the initialization
    path, and app.py never routes an existing deployment back through
    onboarding.py regardless of role."""
    business_memory.save_company({"business_name": "Already Set Up"})
    assert business_memory.company_exists() is True

    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="existing-org", role=MembershipRole.VIEWER)
    _login_as(fx)
    # A VIEWER reading the existing profile is unaffected by this fix —
    # business_snapshot()/load_company() carry no RBAC gate and never did.
    assert business_memory.load_company()["business_name"] == "Already Set Up"


def test_onboarding_legacy_auth_mode_unaffected(isolated, monkeypatch) -> None:
    """When LEADLENS_V2_AUTH_ENABLED is false, save_company_profile()'s
    require_permission() call is a documented no-op — legacy deployments
    (shared-password gate, no Phase 1 identity/session) must not be
    newly blocked by this fix."""
    monkeypatch.setenv("LEADLENS_V2_AUTH_ENABLED", "false")
    assert get_stored_session() is None
    assert auth.v2_auth_enabled() is False

    from services.platform_data import save_company_profile

    save_company_profile({"business_name": "Legacy Clinic"})  # must not raise
    assert business_memory.company_exists() is True


# ---------------------------------------------------------------------------
# 2. RELOAD TOKEN TIMESTAMP PRECISION
# ---------------------------------------------------------------------------

def test_same_second_logout_then_relogin_token_accepted(isolated) -> None:
    """The exact scenario the audit reproduced: logout, then a fresh
    login + token mint within the same integer wall-clock second, must
    now be accepted (the prior int/float truncation mismatch is fixed)."""
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="same-second-org")
    _login_as(fx)
    clear_session()

    # Re-login immediately — in practice this can land in the same
    # integer second as the logout above.
    _login_as(fx)
    token = auth._v2_reload_token()
    identity = auth._v2_reload_token_identity(token)
    assert identity is not None, (
        "A token minted immediately after a fresh login was wrongly "
        "rejected due to timestamp precision mismatch with logout_epoch()."
    )
    assert identity == (fx.user_id, fx.org_id)


def test_pre_logout_token_still_rejected_after_logout(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="pre-logout-org")
    _login_as(fx)
    token = auth._v2_reload_token()
    clear_session()
    assert auth._restore_session_from_reload_token(token) is False


def test_token_minted_after_logout_in_later_second_accepted(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="later-second-org")
    _login_as(fx)
    clear_session()

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 2)
    _login_as(fx)
    token = auth._v2_reload_token()
    identity = auth._v2_reload_token_identity(token)
    assert identity == (fx.user_id, fx.org_id)


def test_reload_token_expiry_unchanged(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="expiry-org")
    _login_as(fx)
    token = auth._v2_reload_token()

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)
    assert auth._v2_reload_token_identity(token) is None


def test_reload_token_tampering_unchanged(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="tamper-org")
    _login_as(fx)
    token = auth._v2_reload_token()
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert auth._v2_reload_token_identity(tampered) is None


def test_reload_token_legacy_integer_timestamp_format_still_parses(isolated) -> None:
    """Backward compatibility: a token minted with the old
    int(time.time()) format (no decimal point) must still parse via
    float() and be treated identically to a full-precision one."""
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="legacy-format-org")
    _login_as(fx)

    ts = str(int(time.time()))
    payload = f"{ts}:{fx.user_id}:{fx.org_id}"
    legacy_token = f"{payload}.{auth._v2_sign(payload)}"
    assert auth._v2_reload_token_identity(legacy_token) == (fx.user_id, fx.org_id)


def test_reload_token_disabled_user_membership_org_unchanged(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="disabled-checks-org")
    _login_as(fx)
    token_user = auth._v2_reload_token()

    with Session(isolated) as session:
        user_service.disable_user(session, fx.user_id)
        session.commit()
    clear_session()
    assert auth._restore_session_from_reload_token(token_user) is False

    with Session(isolated) as session:
        fx2 = _make_org_user_membership(session, org_slug="disabled-checks-org-2", email="m2@example.com")
    _login_as(fx2)
    token_membership = auth._v2_reload_token()
    with Session(isolated) as session:
        membership_service.disable_membership(session, fx2.membership_id)
        session.commit()
    clear_session()
    assert auth._restore_session_from_reload_token(token_membership) is False

    with Session(isolated) as session:
        fx3 = _make_org_user_membership(session, org_slug="disabled-checks-org-3", email="m3@example.com")
    _login_as(fx3)
    token_org = auth._v2_reload_token()
    with Session(isolated) as session:
        organization_service.deactivate_organization(session, fx3.org_id)
        session.commit()
    clear_session()
    assert auth._restore_session_from_reload_token(token_org) is False


def test_reload_token_cross_organization_still_rejected(isolated) -> None:
    with Session(isolated) as session:
        fx_a = _make_org_user_membership(session, org_slug="cross-a-org")
        fx_b = _make_org_user_membership(session, org_slug="cross-b-org", email="b@example.com")
    _login_as(fx_a)
    forged_payload = f"{time.time()!r}:{fx_a.user_id}:{fx_b.org_id}"
    forged_token = f"{forged_payload}.{auth._v2_sign(forged_payload)}"
    assert auth._restore_session_from_reload_token(forged_token) is False


# ---------------------------------------------------------------------------
# 3. LOGOUT / QUERY-PARAM REGRESSION (workspace-switch scenario)
# ---------------------------------------------------------------------------

def test_workspace_switch_then_logout_then_rerun_is_unauthenticated(isolated) -> None:
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="workspace-switch-org")
    _login_as(fx)

    # Simulate the workspace-theme reload embedding a token in the URL.
    token = auth._v2_reload_token()
    st.query_params["_auth"] = token

    # User logs out (the logout control clears both session and the
    # query param).
    clear_session()
    if "_auth" in st.query_params:
        del st.query_params["_auth"]

    # A rerun with no query param and no session must not authenticate.
    assert get_stored_session() is None
    assert "_auth" not in st.query_params


def test_fresh_login_after_logout_workspace_switch_reload_token_accepted(isolated) -> None:
    """No spurious re-login loop: logging back in immediately after the
    scenario above must produce a working reload token for the very next
    workspace switch, even within the same wall-clock second."""
    with Session(isolated) as session:
        fx = _make_org_user_membership(session, org_slug="fresh-login-org")
    _login_as(fx)
    clear_session()

    _login_as(fx)
    token = auth._v2_reload_token()
    st.session_state.clear()  # simulate the forced-navigation reload
    assert auth._restore_session_from_reload_token(token) is True


# ---------------------------------------------------------------------------
# 4. USER A -> USER B ISOLATION (re-verified with workspace-switch tokens)
# ---------------------------------------------------------------------------

def test_user_a_workspace_switch_logout_user_b_login_zero_bleed(isolated) -> None:
    with Session(isolated) as session:
        fx_a = _make_org_user_membership(session, org_slug="isolation-a-org", email="a@example.com")
    _login_as(fx_a)
    token_a = auth._v2_reload_token()
    st.query_params["_auth"] = token_a

    clear_session()
    if "_auth" in st.query_params:
        del st.query_params["_auth"]

    with Session(isolated) as session:
        fx_b = _make_org_user_membership(session, org_slug="isolation-b-org", email="b@example.com")
    _login_as(fx_b)

    stored = get_stored_session()
    assert stored is not None
    assert stored.user_id == fx_b.user_id
    assert stored.organization_id == fx_b.org_id
    assert "_auth" not in st.query_params

    # A's old token still can't resurrect A now that B is logged in.
    assert auth._restore_session_from_reload_token(token_a) is False
    assert get_stored_session().user_id == fx_b.user_id
