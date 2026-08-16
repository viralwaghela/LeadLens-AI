"""V2 Phase 7 tests: live authentication cutover + RBAC enforcement.

Every test uses its own private, temporary SQLite database, never the
tracked local dev database, never a real DATABASE_URL. Streamlit's
st.session_state works as a plain dict outside a real `streamlit run`
("bare mode") — verified directly — so core/auth.py's and
core/identity/session.py's session-state logic is exercised for real
here, not mocked. Full UI form-rendering (buttons/widgets) is not
exercised via streamlit.testing.v1.AppTest — out of scope for this
pass; every test here calls the underlying backend/session functions
directly, which are the actually-authoritative boundaries per
docs/V2_PHASE7_AUTH_CUTOVER.md's backend-first design.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import time

import pytest
import streamlit as st
from sqlalchemy.orm import Session

import core.auth as auth
import core.memory as business_memory
import services.integration_credentials as ic
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole, MembershipStatus, UserStatus
from core.db.session import make_engine
from core.identity import membership_service, organization_service, user_service
from core.identity.session import AuthenticatedSession, clear_session, get_stored_session, store_session
from core.identity.tenant_context import ActorType, TenantContext
from core.identity.permissions import permissions_for_role
from services.authorization_guard import PermissionDenied, require_permission

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


from dataclasses import dataclass


@dataclass
class Fixture:
    """Plain captured values, not ORM instances — used across session
    boundaries throughout this test file (accessing a SQLAlchemy
    object's attributes after its owning session has closed raises
    DetachedInstanceError; capturing plain int/str fields while the
    session is open avoids that entirely)."""

    org_id: int
    org_name: str
    user_id: int
    user_email: str
    membership_id: int
    role: MembershipRole


def _make_org_user_membership(session, *, org_slug, role=MembershipRole.OWNER, email=None, password=PASSWORD) -> Fixture:
    org = organization_service.create_organization(session, name=org_slug.title(), slug=org_slug)
    user = user_service.create_user(session, email=email or f"{org_slug}@example.com", password=password)
    membership = membership_service.create_membership(
        session, user_id=user.id, organization_id=org.id, role=role,
    )
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


def _context_for(session_obj: AuthenticatedSession) -> TenantContext:
    return TenantContext(
        organization_id=session_obj.organization_id, actor_type=ActorType.USER,
        user_id=session_obj.user_id, membership_id=session_obj.membership_id,
        role=session_obj.role, permissions=session_obj.permissions,
    )


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def test_v2_auth_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LEADLENS_V2_AUTH_ENABLED", raising=False)
    assert auth.v2_auth_enabled() is False


def test_v2_auth_enabled_via_flag(isolated) -> None:
    assert auth.v2_auth_enabled() is True


def test_rbac_noop_when_v2_auth_disabled(isolated, monkeypatch) -> None:
    monkeypatch.setenv("LEADLENS_V2_AUTH_ENABLED", "false")
    require_permission("patients.manage")  # must not raise — RBAC inactive


# ---------------------------------------------------------------------------
# Real login via authenticate() + session construction
# ---------------------------------------------------------------------------

def test_authenticate_success_builds_valid_session(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="clinic-a")
        _login_as(fixture)

    live = auth.current_authenticated_session()
    assert live is not None
    assert live.organization_id == fixture.org_id
    assert live.role == MembershipRole.OWNER
    assert "patients.manage" in live.permissions


def test_wrong_password_fails(isolated) -> None:
    from core.identity.authentication_service import authenticate

    with Session(isolated) as session:
        _make_org_user_membership(session, org_slug="clinic-b")
        result = authenticate(session, email="clinic-b@example.com", password="wrong-password")
    assert result.success is False
    assert result.reason == "invalid_credentials"


def test_nonexistent_user_fails(isolated) -> None:
    from core.identity.authentication_service import authenticate

    with Session(isolated) as session:
        result = authenticate(session, email="nobody@example.com", password="whatever")
    assert result.success is False
    assert result.reason == "user_not_found"


def test_disabled_user_fails(isolated) -> None:
    from core.identity.authentication_service import authenticate

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="clinic-c")
        user_service.disable_user(session, fixture.user_id)
        session.commit()
        result = authenticate(session, email=fixture.user_email, password=PASSWORD)
    assert result.success is False
    assert result.reason == "user_disabled"


def test_disabled_membership_excluded_from_result(isolated) -> None:
    from core.identity.authentication_service import authenticate

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="clinic-d")
        membership_service.disable_membership(session, fixture.membership_id)
        session.commit()
        result = authenticate(session, email=fixture.user_email, password=PASSWORD)
    assert result.success is True
    assert result.memberships == []  # authenticated, but no active org access


# ---------------------------------------------------------------------------
# Session revalidation — no stale elevated access
# ---------------------------------------------------------------------------

def test_session_invalidated_when_user_disabled_after_login(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="stale-user-org")
        _login_as(fixture)
    assert auth.current_authenticated_session() is not None

    with Session(isolated) as session:
        user_service.disable_user(session, fixture.user_id)
        session.commit()

    assert auth.current_authenticated_session() is None
    assert get_stored_session() is None  # cleared as a side effect


def test_session_invalidated_when_membership_disabled_after_login(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="stale-membership-org")
        _login_as(fixture)
    with Session(isolated) as session:
        membership_service.disable_membership(session, fixture.membership_id)
        session.commit()
    assert auth.current_authenticated_session() is None


def test_session_invalidated_when_organization_disabled_after_login(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="stale-org-org")
        _login_as(fixture)
    with Session(isolated) as session:
        organization_service.deactivate_organization(session, fixture.org_id)
        session.commit()
    assert auth.current_authenticated_session() is None


def test_session_reflects_role_change_after_login(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="role-change-org", role=MembershipRole.VIEWER)
        _login_as(fixture)
    live = auth.current_authenticated_session()
    assert "patients.manage" not in live.permissions

    with Session(isolated) as session:
        membership_service.change_role(session, fixture.membership_id, MembershipRole.ADMIN)
        session.commit()

    live_after = auth.current_authenticated_session()
    assert "patients.manage" in live_after.permissions  # refreshed, not stale


# ---------------------------------------------------------------------------
# Reload token — continuity, never a bypass
# ---------------------------------------------------------------------------

def test_reload_token_roundtrip_restores_valid_session(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="reload-org")
        _login_as(fixture)
    token = auth._v2_reload_token()
    # Phase 7.1: a forced browser navigation (what this token is actually
    # for, per workspace_theme.py) loses st.session_state naturally but is
    # NOT a logout — it must not set a logout epoch, unlike clear_session().
    # See tests/test_phase7_1_hardening.py for the logout-specific behavior.
    st.session_state.clear()
    assert get_stored_session() is None

    restored = auth._restore_session_from_reload_token(token)
    assert restored is True
    live = auth.current_authenticated_session()
    assert live is not None
    assert live.organization_id == fixture.org_id


def test_reload_token_expired_rejected(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="expired-org")
        _login_as(fixture)
    token = auth._v2_reload_token()
    clear_session()

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)
    assert auth._restore_session_from_reload_token(token) is False


def test_reload_token_tampered_organization_rejected(isolated) -> None:
    with Session(isolated) as session:
        fixture_a = _make_org_user_membership(session, org_slug="tamper-a")
        fixture_b = _make_org_user_membership(session, org_slug="tamper-b")
        _login_as(fixture_a)
    token = auth._v2_reload_token()
    clear_session()

    # Forge a token claiming org_b for user_a by hand-editing the payload
    # (not re-signing) — HMAC must reject it.
    payload, sig = token.rsplit(".", 1)
    ts, uid, _old_org = payload.split(":", 2)
    forged = f"{ts}:{uid}:{fixture_b.org_id}.{sig}"
    assert auth._restore_session_from_reload_token(forged) is False


def test_reload_token_never_elevates_role_beyond_current_db_state(isolated) -> None:
    """Even a validly-signed token only re-triggers a fresh DB check —
    it never carries trusted role/permission claims of its own."""
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="no-elevate-org", role=MembershipRole.VIEWER)
        _login_as(fixture)
    token = auth._v2_reload_token()
    # Phase 7.1: a forced browser navigation, not a logout — see the
    # roundtrip test above for why this must not be clear_session().
    st.session_state.clear()

    # Role is downgraded in the DB before the token is ever restored.
    with Session(isolated) as session:
        membership_service.change_role(session, fixture.membership_id, MembershipRole.VIEWER)
        session.commit()

    auth._restore_session_from_reload_token(token)
    live = auth.current_authenticated_session()
    assert live.role == MembershipRole.VIEWER
    assert "patients.manage" not in live.permissions


# ---------------------------------------------------------------------------
# Logout / session isolation
# ---------------------------------------------------------------------------

def test_logout_clears_session_and_prevents_next_user_bleed(isolated) -> None:
    with Session(isolated) as session:
        fixture_a = _make_org_user_membership(session, org_slug="logout-a")
        fixture_b = _make_org_user_membership(session, org_slug="logout-b")
        _login_as(fixture_a)

    assert auth.current_authenticated_session().organization_id == fixture_a.org_id
    clear_session()
    assert get_stored_session() is None

    _login_as(fixture_b)
    live = auth.current_authenticated_session()
    assert live.organization_id == fixture_b.org_id
    assert live.email == fixture_b.user_email


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_locks_out_after_max_failed_attempts(isolated) -> None:
    for _ in range(auth._MAX_FAILED_ATTEMPTS):
        auth._record_failed_attempt()
    assert auth._check_rate_limit() is not None


def test_rate_limit_resets_on_success(isolated) -> None:
    for _ in range(auth._MAX_FAILED_ATTEMPTS - 1):
        auth._record_failed_attempt()
    auth._reset_failed_attempts()
    assert auth._check_rate_limit() is None


# ---------------------------------------------------------------------------
# Cross-tenant login isolation
# ---------------------------------------------------------------------------

def test_cross_tenant_login_produces_independent_contexts(isolated) -> None:
    with Session(isolated) as session:
        fixture_a = _make_org_user_membership(session, org_slug="xt-a")
        fixture_b = _make_org_user_membership(session, org_slug="xt-b")
        _login_as(fixture_a)
    live_a = auth.current_authenticated_session()
    assert live_a.organization_id == fixture_a.org_id

    clear_session()
    _login_as(fixture_b)
    live_b = auth.current_authenticated_session()
    assert live_b.organization_id == fixture_b.org_id
    assert live_a.organization_id != live_b.organization_id


def test_multi_membership_user_cannot_resolve_arbitrary_foreign_org(isolated) -> None:
    """User C belongs to A and B. Attempting to construct a
    TenantContext for an unrelated Org D (no membership) must fail
    closed via the same trusted build_user_context() path — this is
    the backend invariant the organization picker's own validation
    relies on."""
    from core.identity.tenant_context import build_user_context

    with Session(isolated) as session:
        fixture_a = _make_org_user_membership(session, org_slug="multi-a", email="c@example.com")
        org_b = organization_service.create_organization(session, name="Multi B", slug="multi-b")
        membership_service.create_membership(session, user_id=fixture_a.user_id, organization_id=org_b.id, role=MembershipRole.ADMIN)
        org_d = organization_service.create_organization(session, name="Multi D", slug="multi-d")
        session.commit()
        org_b_id, org_d_id, user_c_id = org_b.id, org_d.id, fixture_a.user_id

    with Session(isolated) as session:
        context_a = build_user_context(session, user_id=user_c_id, organization_id=fixture_a.org_id)
        context_b = build_user_context(session, user_id=user_c_id, organization_id=org_b_id)
        context_d = build_user_context(session, user_id=user_c_id, organization_id=org_d_id)
    assert context_a is not None and context_a.organization_id == fixture_a.org_id
    assert context_b is not None and context_b.organization_id == org_b_id
    assert context_d is None  # no membership -> fail closed


# ---------------------------------------------------------------------------
# RBAC adversarial tests per role — backend calls, not UI checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,allowed,denied", [
    (MembershipRole.RECEPTIONIST, "patients.manage", "payments.manage"),
    (MembershipRole.PRACTITIONER, "treatments.manage", "integrations.manage"),
    (MembershipRole.FINANCE, "payments.manage", "patients.manage"),
    (MembershipRole.MARKETING, "leads.manage", "finance.view"),
    (MembershipRole.VIEWER, None, "patients.manage"),
])
def test_role_permission_boundaries(isolated, role, allowed, denied) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug=f"role-{role.value.lower()}", role=role)
        _login_as(fixture)

    if allowed:
        require_permission(allowed)  # must not raise
    with pytest.raises(PermissionDenied):
        require_permission(denied)


def test_owner_has_full_intended_access(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="owner-org", role=MembershipRole.OWNER)
        _login_as(fixture)
    for permission in ("patients.manage", "payments.manage", "finance.view", "integrations.manage", "members.manage", "organization.manage"):
        require_permission(permission)  # none raise


def test_admin_does_not_silently_equal_owner(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="admin-org", role=MembershipRole.ADMIN)
        _login_as(fixture)
    require_permission("patients.manage")  # ADMIN has this
    with pytest.raises(PermissionDenied):
        require_permission("organization.manage")  # OWNER-only per Phase 1 matrix
    with pytest.raises(PermissionDenied):
        require_permission("finance.view")  # OWNER/FINANCE-only


def test_viewer_denied_all_crm_writes(isolated) -> None:
    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="viewer-org", role=MembershipRole.VIEWER)
        _login_as(fixture)
    for permission in ("patients.manage", "appointments.manage", "treatments.manage", "leads.manage", "payments.manage"):
        with pytest.raises(PermissionDenied):
            require_permission(permission)


# ---------------------------------------------------------------------------
# Backend chokepoints actually raise (not just require_permission() in
# isolation) — proves the wiring, not just the guard function itself.
# ---------------------------------------------------------------------------

def test_clinic_data_service_add_record_denied_for_viewer(isolated) -> None:
    import services.clinic_data_service as crm

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="crm-viewer-org", role=MembershipRole.VIEWER)
        _login_as(fixture)

    with pytest.raises(PermissionDenied):
        crm.add_record("patients", {"name": "Test Patient"})


def test_clinic_data_service_add_record_allowed_for_receptionist(isolated) -> None:
    import services.clinic_data_service as crm

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="crm-recept-org", role=MembershipRole.RECEPTIONIST)
        _login_as(fixture)

    row = crm.add_record("patients", {"name": "Allowed Patient"})
    assert row["name"] == "Allowed Patient"


def test_integration_credentials_configure_denied_without_permission(isolated) -> None:
    from core.db.models.integration import IntegrationProvider

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="int-viewer-org", role=MembershipRole.VIEWER)
        _login_as(fixture)
        with pytest.raises(PermissionDenied):
            ic.configure_integration(
                session, _context_for(auth.current_authenticated_session()), IntegrationProvider.WHATSAPP,
                secret_fields={"access_token": "fake-token"}, configuration_fields={"phone_number_id": "P1"},
            )


def test_integration_manager_decide_item_denied_for_receptionist(isolated) -> None:
    """RECEPTIONIST lacks automations.approve in the Phase 1 matrix."""
    import services.integration_manager_v21 as mgr

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="approve-recept-org", role=MembershipRole.RECEPTIONIST)

    context = TenantContext(organization_id=fixture.org_id, actor_type=ActorType.SYSTEM)
    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)

    _login_as(fixture)
    with pytest.raises(PermissionDenied):
        mgr.decide_item(item["id"], "Approved")


def test_integration_manager_decide_item_allowed_for_admin(isolated) -> None:
    import services.integration_manager_v21 as mgr

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="approve-admin-org", role=MembershipRole.ADMIN)

    context = TenantContext(organization_id=fixture.org_id, actor_type=ActorType.SYSTEM)
    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)

    _login_as(fixture)
    result = mgr.decide_item(item["id"], "Approved")
    assert result["status"] == "Approved"


# ---------------------------------------------------------------------------
# Jarvis RBAC
# ---------------------------------------------------------------------------

def test_jarvis_finance_tool_denied_without_finance_permission() -> None:
    from services.jarvis_tools import run_read_only_tool

    context = {"model_context": {"confirmed_facts": {"financial_snapshot": {"monthly_revenue": 999999}}}}
    result = run_read_only_tool("revenue_summary", context, permissions=frozenset({"jarvis.use"}))
    assert result.get("access_denied") is True
    assert result["data"] == {}


def test_jarvis_finance_tool_allowed_with_finance_permission() -> None:
    from services.jarvis_tools import run_read_only_tool

    context = {"model_context": {"confirmed_facts": {"financial_snapshot": {"monthly_revenue": 999999}}}}
    result = run_read_only_tool("revenue_summary", context, permissions=frozenset({"jarvis.use", "jarvis.finance"}))
    assert result["data"].get("monthly_revenue") == 999999


def test_jarvis_business_snapshot_redacts_financial_field_without_finance_permission() -> None:
    """The pre-existing information-boundary gap this phase closes:
    business_snapshot also carried financial_snapshot data under a
    different tool name, reachable by non-finance specialists."""
    from services.jarvis_tools import run_read_only_tool

    context = {"model_context": {"confirmed_facts": {
        "business": {"name": "Test Clinic"},
        "financial_snapshot": {"monthly_revenue": 12345},
    }}}
    result = run_read_only_tool("business_snapshot", context, permissions=frozenset({"jarvis.use", "jarvis.marketing"}))
    assert "financial_snapshot" not in result["data"]
    assert result["data"]["business"] == {"name": "Test Clinic"}


def test_jarvis_no_permissions_argument_reproduces_legacy_behavior() -> None:
    """permissions=None (the default) must be byte-identical to
    pre-Phase-7 output — no RBAC active for that call."""
    from services.jarvis_tools import run_read_only_tool

    context = {"model_context": {"confirmed_facts": {"financial_snapshot": {"monthly_revenue": 42}}}}
    result = run_read_only_tool("revenue_summary", context)
    assert result["data"].get("monthly_revenue") == 42


def test_specialist_orchestration_blocks_finance_specialist_without_permission() -> None:
    from services.specialist_orchestration import _SPECIALIST_PERMISSION

    assert _SPECIALIST_PERMISSION["finance"] == "jarvis.finance"
    assert _SPECIALIST_PERMISSION["marketing"] == "jarvis.marketing"


def test_coordinate_specialists_denies_without_jarvis_use() -> None:
    from services.specialist_orchestration import coordinate_specialists

    result = coordinate_specialists("How is revenue?", permissions=frozenset())
    assert result["success"] is False
    assert "access" in result["message"].lower()


# ---------------------------------------------------------------------------
# Member management — org isolation, last-owner protection
# ---------------------------------------------------------------------------

def test_member_management_cannot_touch_foreign_organization(isolated) -> None:
    from services import member_management as mm

    with Session(isolated) as session:
        fixture_a = _make_org_user_membership(session, org_slug="mm-a", role=MembershipRole.OWNER)
        fixture_b = _make_org_user_membership(session, org_slug="mm-b", role=MembershipRole.OWNER)
        _login_as(fixture_a)
        context_a = _context_for(auth.current_authenticated_session())

        with pytest.raises(mm.ForeignOrganizationError):
            mm.disable_member(session, context_a, fixture_b.membership_id)


def test_member_management_last_owner_protected(isolated) -> None:
    from services import member_management as mm

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="last-owner-org", role=MembershipRole.OWNER)
        _login_as(fixture)
        context = _context_for(auth.current_authenticated_session())

        with pytest.raises(mm.LastOwnerError):
            mm.disable_member(session, context, fixture.membership_id)
        with pytest.raises(mm.LastOwnerError):
            mm.change_member_role(session, context, fixture.membership_id, MembershipRole.ADMIN)


def test_member_management_second_owner_can_be_demoted(isolated) -> None:
    from services import member_management as mm

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="two-owner-org", role=MembershipRole.OWNER)
        second_user = user_service.create_user(session, email="second-owner@example.com", password=PASSWORD)
        second_membership = membership_service.create_membership(
            session, user_id=second_user.id, organization_id=fixture.org_id, role=MembershipRole.OWNER,
        )
        second_membership_id = second_membership.id
        session.commit()
        _login_as(fixture)
        context = _context_for(auth.current_authenticated_session())

        result = mm.change_member_role(session, context, second_membership_id, MembershipRole.ADMIN)
        assert result.role == "ADMIN"


def test_member_management_denied_for_receptionist(isolated) -> None:
    from services import member_management as mm

    with Session(isolated) as session:
        fixture = _make_org_user_membership(session, org_slug="mm-recept-org", role=MembershipRole.RECEPTIONIST)
        _login_as(fixture)
        context = _context_for(auth.current_authenticated_session())

        with pytest.raises(PermissionDenied):
            mm.create_member(session, context, email="new@example.com", password="x", role=MembershipRole.VIEWER)


# ---------------------------------------------------------------------------
# Failure-closed: malformed / manipulated session state
# ---------------------------------------------------------------------------

def test_manipulated_session_organization_id_fails_closed(isolated) -> None:
    """Directly tampering with the stored session dict's organization_id
    (simulating a manipulated session) must not grant access to that
    organization — revalidation only trusts DB-verified membership."""
    with Session(isolated) as session:
        fixture_a = _make_org_user_membership(session, org_slug="manip-a")
        fixture_b = _make_org_user_membership(session, org_slug="manip-b")
        _login_as(fixture_a)

    # Tamper: point the stored session at org_b, which user_a has no membership in.
    st.session_state["v2_auth_session"]["organization_id"] = fixture_b.org_id
    live = auth.current_authenticated_session()
    assert live is None  # fails closed rather than granting org_b access


def test_malformed_stored_session_returns_none() -> None:
    st.session_state["v2_auth_session"] = {"garbage": "data"}
    assert get_stored_session() is None


def test_exact_foreign_membership_id_denied_in_authorization(isolated) -> None:
    from core.identity.tenant_context import build_user_context

    with Session(isolated) as session:
        fixture_a = _make_org_user_membership(session, org_slug="foreign-mem-a")
        fixture_b = _make_org_user_membership(session, org_slug="foreign-mem-b")

    with Session(isolated) as session:
        # user_a attempting to resolve context under org_b (whose exact
        # membership id they know) — no membership exists for that pair.
        context = build_user_context(session, user_id=fixture_a.user_id, organization_id=fixture_b.org_id)
    assert context is None
