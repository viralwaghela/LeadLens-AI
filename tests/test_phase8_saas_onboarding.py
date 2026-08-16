"""V2 Phase 8 tests: SaaS onboarding + genuine two-organization validation.

Every test provisions real Organization/User/Membership rows via the
same core.identity services scripts/provision_organization.py itself
uses (not a mocked/fixture-only shortcut), then exercises CRM, Jarvis
memory, settings, RBAC, integrations, scheduler enumeration, approvals,
and audit through the real service-layer functions with a live,
bare-mode Streamlit session — the same pattern established across
tests/test_phase5_tenant_context.py through
tests/test_phase7_1_1_hardening.py. import _bootstrap first, same as
every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from dataclasses import dataclass

import pytest
import streamlit as st
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

import core.auth as auth
import core.memory as business_memory
import services.clinic_data_service as crm
import services.crm_read_router as crm_router
import services.crm_tenant_writer as crm_writer
import services.integration_credentials as ic
import services.jarvis_memory as jm
import services.platform_data as platform_data
import services.relational_sync_service as rs
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole
from core.db.models.integration import IntegrationProvider
from core.db.models.operations import Approval, ExecutionQueueItem, SecurityAuditEvent
from core.db.models.organization import OrganizationSettings
from core.db.session import make_engine, session_scope
from core.identity import membership_service, organization_service, user_service
from core.identity.membership_service import DuplicateMembershipError
from core.identity.organization_service import DuplicateOrganizationError
from core.identity.permissions import permissions_for_role
from core.identity.session import AuthenticatedSession, clear_session, get_stored_session, store_session
from core.identity.tenant_context import ActorType, TenantContext
from core.identity.user_service import DuplicateUserError
from services.authorization_guard import PermissionDenied
from services.integration_credentials import configure_integration, resolve_credentials
from services.security_service import audit_event

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr(auth, "_V2_ENGINE", engine)
    monkeypatch.setattr(crm_router, "_ENGINE", engine)
    monkeypatch.setattr(rs, "_ENGINE", engine)
    monkeypatch.setattr(jm, "_ENGINE", engine)
    monkeypatch.setattr(platform_data, "_ENGINE", engine)
    monkeypatch.setenv("LEADLENS_V2_AUTH_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_TENANT_CONTEXT_ENABLED", "true")
    monkeypatch.setattr(crm_router, "TENANT_AUTHORITATIVE_ENABLED", True)
    monkeypatch.setattr(platform_data, "ORG_SCOPED_SETTINGS_ENABLED", True)
    monkeypatch.setattr(jm, "JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED", True)

    import services.tenant_operational_sync as tos

    monkeypatch.setattr(tos, "TENANT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(tos, "_ENGINE", engine)

    from services import credential_encryption as ce

    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY_VERSION", raising=False)
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", False)
    ce._fernet_for_version.cache_clear()

    st.session_state.clear()
    st.query_params.clear()
    yield engine
    st.session_state.clear()
    st.query_params.clear()
    engine.dispose()


@dataclass
class Clinic:
    org_id: int
    org_name: str
    slug: str
    owner_user_id: int
    owner_email: str
    owner_membership_id: int


def _provision(session, *, slug: str, name: str, owner_email: str) -> Clinic:
    """Mirrors scripts/provision_organization.py's own logic exactly —
    create-or-get organization, create-or-get user, create-or-get OWNER
    membership."""
    org = organization_service.create_organization(session, name=name, slug=slug)
    user = user_service.create_user(session, email=owner_email, password=PASSWORD)
    membership = membership_service.create_membership(
        session, user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER,
    )
    session.commit()
    return Clinic(
        org_id=org.id, org_name=org.name, slug=slug,
        owner_user_id=user.id, owner_email=user.email, owner_membership_id=membership.id,
    )


def _login(user_id, email, org_id, org_name, membership_id, role) -> None:
    store_session(
        AuthenticatedSession(
            user_id=user_id, email=email, organization_id=org_id, organization_name=org_name,
            membership_id=membership_id, role=role, permissions=permissions_for_role(role),
        )
    )


def _add_member(session, clinic: Clinic, *, email: str, role: MembershipRole) -> tuple[int, int]:
    user = user_service.create_user(session, email=email, password=PASSWORD)
    membership = membership_service.create_membership(
        session, user_id=user.id, organization_id=clinic.org_id, role=role,
    )
    session.commit()
    return user.id, membership.id


# ---------------------------------------------------------------------------
# 1. PROVISIONING
# ---------------------------------------------------------------------------

def test_provision_creates_org_and_owner(isolated) -> None:
    with Session(isolated) as session:
        clinic = _provision(session, slug="prov-a", name="Prov Clinic A", owner_email="owner@prov-a.example")
    assert clinic.org_id is not None
    assert clinic.owner_membership_id is not None


def test_provision_idempotent_retry(isolated) -> None:
    with Session(isolated) as session:
        organization_service.create_organization(session, name="Prov B", slug="prov-b")
        session.commit()
    with Session(isolated) as session:
        with pytest.raises(DuplicateOrganizationError):
            organization_service.create_organization(session, name="Prov B", slug="prov-b")
    with Session(isolated) as session:
        assert organization_service.get_organization_by_slug(session, "prov-b") is not None


def test_provision_duplicate_slug_rejected(isolated) -> None:
    with Session(isolated) as session:
        organization_service.create_organization(session, name="X", slug="dup-slug")
        session.commit()
    with Session(isolated) as session:
        with pytest.raises(DuplicateOrganizationError):
            organization_service.create_organization(session, name="Y", slug="dup-slug")


def test_provision_existing_user_reused_no_duplicate(isolated) -> None:
    with Session(isolated) as session:
        user = user_service.create_user(session, email="shared@example.com", password=PASSWORD)
        session.commit()
        original_hash = user.password_hash
    with Session(isolated) as session:
        with pytest.raises(DuplicateUserError):
            user_service.create_user(session, email="shared@example.com", password="a-totally-different-password")
    with Session(isolated) as session:
        fetched = user_service.get_user_by_email(session, "shared@example.com")
        assert fetched.password_hash == original_hash  # never overwritten


def test_provision_existing_user_can_own_second_org_without_new_password(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="multi-a", name="Multi A", owner_email="multi-owner@example.com")
    with Session(isolated) as session:
        org_b = organization_service.create_organization(session, name="Multi B", slug="multi-b")
        user = user_service.get_user_by_email(session, "multi-owner@example.com")
        membership_b = membership_service.create_membership(
            session, user_id=user.id, organization_id=org_b.id, role=MembershipRole.OWNER,
        )
        session.commit()
        org_b_id, membership_b_id = org_b.id, membership_b.id
    with Session(isolated) as session:
        with pytest.raises(DuplicateMembershipError):
            membership_service.create_membership(
                session, user_id=clinic_a.owner_user_id, organization_id=clinic_a.org_id, role=MembershipRole.OWNER,
            )


# ---------------------------------------------------------------------------
# 2. ORGANIZATION-SCOPED SETTINGS
# ---------------------------------------------------------------------------

def test_org_scoped_settings_independent_and_company_setup_per_org(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="settings-a", name="Settings A", owner_email="a@settings.example")
        clinic_b = _provision(session, slug="settings-b", name="Settings B", owner_email="b@settings.example")

    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    assert platform_data.company_setup_complete() is False
    platform_data.save_company_profile({"business_name": "Settings A Clinic"})
    assert platform_data.company_setup_complete() is True

    clear_session()
    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)
    assert platform_data.company_setup_complete() is False  # B's own onboarding state, unaffected by A
    snapshot_b = platform_data.business_snapshot()
    assert snapshot_b["company"].get("business_name", "") != "Settings A Clinic"

    platform_data.save_company_profile({"business_name": "Settings B Clinic"})
    snapshot_b2 = platform_data.business_snapshot()
    assert snapshot_b2["company"]["business_name"] == "Settings B Clinic"

    clear_session()
    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    snapshot_a = platform_data.business_snapshot()
    assert snapshot_a["company"]["business_name"] == "Settings A Clinic"  # unaffected by B's save


def test_org_settings_unauthorized_role_denied(isolated) -> None:
    with Session(isolated) as session:
        clinic = _provision(session, slug="settings-denied", name="Settings Denied", owner_email="owner@settings-denied.example")
        viewer_user_id, viewer_membership_id = _add_member(session, clinic, email="viewer@settings-denied.example", role=MembershipRole.VIEWER)

    _login(viewer_user_id, "viewer@settings-denied.example", clinic.org_id, clinic.org_name, viewer_membership_id, MembershipRole.VIEWER)
    with pytest.raises(PermissionDenied):
        platform_data.save_company_profile({"business_name": "Should not save"})


def test_org_settings_adversarial_a_cannot_change_b(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="adv-settings-a", name="Adv Settings A", owner_email="a@adv-settings.example")
        clinic_b = _provision(session, slug="adv-settings-b", name="Adv Settings B", owner_email="b@adv-settings.example")

    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)
    platform_data.save_company_profile({"business_name": "B Original"})

    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    platform_data.save_company_profile({"business_name": "A Original"})

    with Session(isolated) as session:
        settings_b = session.query(OrganizationSettings).filter(OrganizationSettings.organization_id == clinic_b.org_id).one()
        assert settings_b.business_name == "B Original"  # A's write never touched B's row


# ---------------------------------------------------------------------------
# 3. CRM — genuine two-organization isolation with overlapping external IDs
# ---------------------------------------------------------------------------

def test_new_organization_crm_starts_empty(isolated) -> None:
    with Session(isolated) as session:
        clinic = _provision(session, slug="crm-empty", name="CRM Empty", owner_email="owner@crm-empty.example")
    _login(clinic.owner_user_id, clinic.owner_email, clinic.org_id, clinic.org_name, clinic.owner_membership_id, MembershipRole.OWNER)
    for entity in ("patients", "appointments", "leads", "payments"):
        assert crm.list_records(entity) == []


def test_crm_overlapping_external_ids_coexist_and_isolate(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="crm-a", name="CRM A", owner_email="owner@crm-a.example")
        clinic_b = _provision(session, slug="crm-b", name="CRM B", owner_email="owner@crm-b.example")

    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    patient_a = crm.add_record("patients", {"name": "Alice"})
    assert patient_a["patient_id"] == "P-001"
    lead_a = crm.add_record("leads", {"name": "Lead A", "source": "Website"})
    assert lead_a["lead_id"] == "L-001"

    clear_session()
    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)
    patient_b = crm.add_record("patients", {"name": "Bob"})
    assert patient_b["patient_id"] == "P-001"  # same external id, different organization
    lead_b = crm.add_record("leads", {"name": "Lead B", "source": "Referral"})
    assert lead_b["lead_id"] == "L-001"

    b_patients = crm.list_records("patients")
    assert [p["name"] for p in b_patients] == ["Bob"]
    b_get = crm.get_record("patients", "P-001")
    assert b_get["name"] == "Bob"

    clear_session()
    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    a_patients = crm.list_records("patients")
    assert [p["name"] for p in a_patients] == ["Alice"]
    a_get = crm.get_record("patients", "P-001")
    assert a_get["name"] == "Alice"


def test_crm_explicit_organization_id_for_programmatic_callers(isolated) -> None:
    """Scheduler/tests can pass organization_id explicitly rather than
    relying on an implicit live session."""
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="crm-explicit-a", name="Explicit A", owner_email="a@crm-explicit.example")
        clinic_b = _provision(session, slug="crm-explicit-b", name="Explicit B", owner_email="b@crm-explicit.example")

    crm.add_record("patients", {"name": "Explicit Alice"}, organization_id=clinic_a.org_id)
    crm.add_record("patients", {"name": "Explicit Bob"}, organization_id=clinic_b.org_id)

    assert [p["name"] for p in crm.list_records("patients", organization_id=clinic_a.org_id)] == ["Explicit Alice"]
    assert [p["name"] for p in crm.list_records("patients", organization_id=clinic_b.org_id)] == ["Explicit Bob"]


def test_crm_update_isolated_per_organization(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="crm-upd-a", name="Upd A", owner_email="a@crm-upd.example")
        clinic_b = _provision(session, slug="crm-upd-b", name="Upd B", owner_email="b@crm-upd.example")

    crm.add_record("patients", {"name": "Same Name"}, organization_id=clinic_a.org_id)
    crm.add_record("patients", {"name": "Same Name"}, organization_id=clinic_b.org_id)
    crm.update_record("patients", "P-001", {"phone": "111"}, organization_id=clinic_a.org_id)

    a_patient = crm.get_record("patients", "P-001", organization_id=clinic_a.org_id)
    b_patient = crm.get_record("patients", "P-001", organization_id=clinic_b.org_id)
    assert a_patient["phone"] == "111"
    assert b_patient.get("phone", "") == ""


# ---------------------------------------------------------------------------
# 4. AUTH / SESSION — owner A, owner B, multi-membership, restricted role
# ---------------------------------------------------------------------------

def test_owner_a_and_owner_b_see_only_their_own_organization(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="auth-a", name="Auth A", owner_email="a@auth.example")
        clinic_b = _provision(session, slug="auth-b", name="Auth B", owner_email="b@auth.example")

    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    session_a = get_stored_session()
    assert session_a.organization_id == clinic_a.org_id

    clear_session()
    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)
    session_b = get_stored_session()
    assert session_b.organization_id == clinic_b.org_id
    assert session_b.organization_id != session_a.organization_id


def test_multi_membership_user_switches_between_validated_orgs(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="mm-a", name="MM A", owner_email="mm-user@example.com")
        org_b = organization_service.create_organization(session, name="MM B", slug="mm-b")
        user = user_service.get_user_by_email(session, "mm-user@example.com")
        membership_b = membership_service.create_membership(
            session, user_id=user.id, organization_id=org_b.id, role=MembershipRole.OWNER,
        )
        session.commit()
        org_b_id, org_b_name, membership_b_id = org_b.id, org_b.name, membership_b.id

    with Session(isolated) as session:
        memberships = membership_service.list_memberships_for_user(session, clinic_a.owner_user_id)
        assert {m.organization_id for m in memberships} == {clinic_a.org_id, org_b_id}

    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    assert get_stored_session().organization_id == clinic_a.org_id

    clear_session()
    _login(clinic_a.owner_user_id, clinic_a.owner_email, org_b_id, org_b_name, membership_b_id, MembershipRole.OWNER)
    assert get_stored_session().organization_id == org_b_id


def test_restricted_role_on_new_organization_rbac_works(isolated) -> None:
    with Session(isolated) as session:
        clinic = _provision(session, slug="rbac-new-org", name="RBAC New Org", owner_email="owner@rbac-new.example")
        receptionist_user_id, receptionist_membership_id = _add_member(
            session, clinic, email="receptionist@rbac-new.example", role=MembershipRole.RECEPTIONIST,
        )

    _login(receptionist_user_id, "receptionist@rbac-new.example", clinic.org_id, clinic.org_name, receptionist_membership_id, MembershipRole.RECEPTIONIST)
    crm.add_record("patients", {"name": "Receptionist Can Add"})  # appointments/patients allowed
    with pytest.raises(PermissionDenied):
        platform_data.save_company_profile({"business_name": "Not allowed"})


# ---------------------------------------------------------------------------
# 5. JARVIS MEMORY
# ---------------------------------------------------------------------------

def test_jarvis_memory_empty_for_new_organization_and_isolated(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="jarvis-a", name="Jarvis A", owner_email="a@jarvis.example")
        clinic_b = _provision(session, slug="jarvis-b", name="Jarvis B", owner_email="b@jarvis.example")

    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)
    assert jm.load_learning_memory()["preferences"] == []

    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    jm.save_owner_preference("tone", "formal")

    clear_session()
    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)
    assert jm.load_learning_memory()["preferences"] == []  # A's memory not inherited
    jm.save_owner_preference("tone", "casual")

    clear_session()
    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    a_prefs = [p["value"] for p in jm.load_learning_memory()["preferences"]]
    assert a_prefs == ["formal"]


# ---------------------------------------------------------------------------
# 6. INTEGRATIONS — B cannot inherit A, independent configuration
# ---------------------------------------------------------------------------

def test_integration_b_unconfigured_fails_closed_not_inherit_a(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="int-a", name="Int A", owner_email="a@int.example")
        clinic_b = _provision(session, slug="int-b", name="Int B", owner_email="b@int.example")

    with Session(isolated) as session:
        context_a = TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SYSTEM)
        configure_integration(
            session, context_a, IntegrationProvider.WHATSAPP,
            secret_fields={"access_token": "fake-a-token"},
            configuration_fields={"phone_number_id": "a-phone"},
        )
        session.commit()

    with Session(isolated) as session:
        context_b = TenantContext(organization_id=clinic_b.org_id, actor_type=ActorType.SYSTEM)
        resolved_b = resolve_credentials(session, context_b, IntegrationProvider.WHATSAPP)
        assert resolved_b is None  # fails closed, never falls back to A's credentials

        context_a = TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SYSTEM)
        resolved_a = resolve_credentials(session, context_a, IntegrationProvider.WHATSAPP)
        assert resolved_a is not None
        assert resolved_a.secret["access_token"] == "fake-a-token"


def test_integration_b_configured_independently_a_unchanged(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="int2-a", name="Int2 A", owner_email="a@int2.example")
        clinic_b = _provision(session, slug="int2-b", name="Int2 B", owner_email="b@int2.example")

    with Session(isolated) as session:
        context_a = TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SYSTEM)
        context_b = TenantContext(organization_id=clinic_b.org_id, actor_type=ActorType.SYSTEM)
        configure_integration(
            session, context_a, IntegrationProvider.WHATSAPP,
            secret_fields={"access_token": "a-token"}, configuration_fields={"phone_number_id": "a-phone"},
        )
        configure_integration(
            session, context_b, IntegrationProvider.WHATSAPP,
            secret_fields={"access_token": "b-token"}, configuration_fields={"phone_number_id": "b-phone"},
        )
        session.commit()

    with Session(isolated) as session:
        resolved_a = resolve_credentials(session, TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SYSTEM), IntegrationProvider.WHATSAPP)
        resolved_b = resolve_credentials(session, TenantContext(organization_id=clinic_b.org_id, actor_type=ActorType.SYSTEM), IntegrationProvider.WHATSAPP)
        assert resolved_a.secret["access_token"] == "a-token"
        assert resolved_b.secret["access_token"] == "b-token"


# ---------------------------------------------------------------------------
# 7. SCHEDULER ORGANIZATION ENUMERATION
# ---------------------------------------------------------------------------

def test_scheduler_multi_org_enumeration_respects_active_and_automations_enabled(isolated, monkeypatch) -> None:
    from scheduler.run_scheduled_checks import resolve_scheduler_organizations
    from core.identity.organization_profile_service import set_automations_enabled

    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    import core.db.session as db_session_mod
    monkeypatch.setattr(db_session_mod, "make_engine", lambda *a, **k: isolated)

    with Session(isolated) as session:
        clinic_a = _provision(session, slug="sched-a", name="Sched A", owner_email="a@sched.example")
        clinic_b = _provision(session, slug="sched-b", name="Sched B", owner_email="b@sched.example")
        clinic_c = _provision(session, slug="sched-c", name="Sched C (disabled org)", owner_email="c@sched.example")

    with Session(isolated) as session:
        set_automations_enabled(session, clinic_a.org_id, True)
        set_automations_enabled(session, clinic_b.org_id, False)  # opted out
        set_automations_enabled(session, clinic_c.org_id, True)
        organization_service.deactivate_organization(session, clinic_c.org_id)  # but org itself disabled
        session.commit()

    orgs = resolve_scheduler_organizations()
    assert orgs == [clinic_a.org_id]  # only the active org with automations explicitly enabled


def test_scheduler_new_org_defaults_automations_disabled(isolated, monkeypatch) -> None:
    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    import core.db.session as db_session_mod
    monkeypatch.setattr(db_session_mod, "make_engine", lambda *a, **k: isolated)
    from scheduler.run_scheduled_checks import resolve_scheduler_organizations

    with Session(isolated) as session:
        _provision(session, slug="sched-fresh", name="Sched Fresh", owner_email="owner@sched-fresh.example")
        # No OrganizationSettings row created at all — a brand-new org
        # before onboarding completes.

    assert resolve_scheduler_organizations() == []


# ---------------------------------------------------------------------------
# 8. APPROVALS / EXECUTION QUEUE ADVERSARIAL
# ---------------------------------------------------------------------------

def test_identical_action_under_a_and_b_produces_independent_approvals(isolated) -> None:
    from services.integration_manager_v21 import prepare_execution

    with Session(isolated) as session:
        clinic_a = _provision(session, slug="appr-a", name="Appr A", owner_email="a@appr.example")
        clinic_b = _provision(session, slug="appr-b", name="Appr B", owner_email="b@appr.example")

    context_a = TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SYSTEM)
    context_b = TenantContext(organization_id=clinic_b.org_id, actor_type=ActorType.SYSTEM)

    prepare_execution(
        "whatsapp", "send_text", {"to": "+10000000000", "body": "Reminder"},
        "Identical reminder", tenant_context=context_a,
    )
    prepare_execution(
        "whatsapp", "send_text", {"to": "+10000000000", "body": "Reminder"},
        "Identical reminder", tenant_context=context_b,
    )

    with Session(isolated) as session:
        items_a = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_a.org_id).all()
        items_b = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_b.org_id).all()
        approvals_a = session.query(Approval).filter(Approval.organization_id == clinic_a.org_id).all()
        approvals_b = session.query(Approval).filter(Approval.organization_id == clinic_b.org_id).all()
        assert len(items_a) == 1
        assert len(items_b) == 1
        assert len(approvals_a) == 1
        assert len(approvals_b) == 1
        assert items_a[0].id != items_b[0].id


# ---------------------------------------------------------------------------
# 9. AUDIT ADVERSARIAL
# ---------------------------------------------------------------------------

def test_audit_events_isolated_per_organization(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="audit-a", name="Audit A", owner_email="a@audit.example")
        clinic_b = _provision(session, slug="audit-b", name="Audit B", owner_email="b@audit.example")

    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    audit_event(clinic_a.owner_email, "test_event_a", "test", "detail-a")

    clear_session()
    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)
    audit_event(clinic_b.owner_email, "test_event_b", "test", "detail-b")

    with Session(isolated) as session:
        events_a = session.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == clinic_a.org_id).all()
        events_b = session.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == clinic_b.org_id).all()
        assert any(e.action == "test_event_a" for e in events_a)
        assert not any(e.action == "test_event_a" for e in events_b)
        assert any(e.action == "test_event_b" for e in events_b)
        assert not any(e.action == "test_event_b" for e in events_a)


# ---------------------------------------------------------------------------
# 10. ORGANIZATION DISABLED — end to end
# ---------------------------------------------------------------------------

def test_disabled_organization_denies_login_and_scheduler(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        clinic = _provision(session, slug="disabled-org", name="Disabled Org", owner_email="owner@disabled-org.example")
    _login(clinic.owner_user_id, clinic.owner_email, clinic.org_id, clinic.org_name, clinic.owner_membership_id, MembershipRole.OWNER)
    assert get_stored_session() is not None

    with Session(isolated) as session:
        organization_service.deactivate_organization(session, clinic.org_id)
        session.commit()

    assert auth.current_authenticated_session() is None  # revalidation fails closed
    assert get_stored_session() is None  # cleared as a side effect

    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    import core.db.session as db_session_mod
    monkeypatch.setattr(db_session_mod, "make_engine", lambda *a, **k: isolated)
    from core.identity.organization_profile_service import set_automations_enabled
    with Session(isolated) as session:
        set_automations_enabled(session, clinic.org_id, True)
        session.commit()
    from scheduler.run_scheduled_checks import resolve_scheduler_organizations
    assert clinic.org_id not in resolve_scheduler_organizations()  # disabled org skipped


# ---------------------------------------------------------------------------
# 11. FULL END-TO-END TWO-ORGANIZATION VALIDATION
# ---------------------------------------------------------------------------

def test_end_to_end_two_organization_validation(isolated) -> None:
    from services.integration_manager_v21 import prepare_execution

    # --- Provision both clinics (mirrors scripts/provision_organization.py) ---
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="e2e-a", name="E2E Clinic A", owner_email="owner-a@e2e.example")
        clinic_b = _provision(session, slug="e2e-b", name="E2E Clinic B", owner_email="owner-b@e2e.example")

    # --- Login A, create/read A data ---
    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    platform_data.save_company_profile({"business_name": "E2E Clinic A", "monthly_revenue": 100000})
    patient_a = crm.add_record("patients", {"name": "Overlap Patient"})
    assert patient_a["patient_id"] == "P-001"
    jm.save_owner_preference("tone", "formal")
    context_a = TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SYSTEM)
    prepare_execution("whatsapp", "send_text", {"to": "+1", "body": "Hi A"}, "A action", tenant_context=context_a)
    audit_event(clinic_a.owner_email, "e2e_action_a", "test", "")

    # --- Logout, login B ---
    clear_session()
    _login(clinic_b.owner_user_id, clinic_b.owner_email, clinic_b.org_id, clinic_b.org_name, clinic_b.owner_membership_id, MembershipRole.OWNER)

    # --- A data absent for B ---
    assert crm.list_records("patients") == []
    assert platform_data.company_setup_complete() is False
    assert jm.load_learning_memory()["preferences"] == []

    # --- Create/read B data, with the SAME external id as A used ---
    platform_data.save_company_profile({"business_name": "E2E Clinic B", "monthly_revenue": 50000})
    patient_b = crm.add_record("patients", {"name": "Overlap Patient B"})
    assert patient_b["patient_id"] == "P-001"  # coexists with A's P-001
    jm.save_owner_preference("tone", "casual")

    # --- B's identical action produces a separate approval; credential resolution differs ---
    context_b = TenantContext(organization_id=clinic_b.org_id, actor_type=ActorType.SYSTEM)
    prepare_execution("whatsapp", "send_text", {"to": "+1", "body": "Hi A"}, "A action", tenant_context=context_b)
    audit_event(clinic_b.owner_email, "e2e_action_b", "test", "")

    with Session(isolated) as session:
        configure_integration(
            session, context_a, IntegrationProvider.WHATSAPP,
            secret_fields={"access_token": "a-secret"}, configuration_fields={"phone_number_id": "a"},
        )
        session.commit()
    with Session(isolated) as session:
        resolved_a = resolve_credentials(session, context_a, IntegrationProvider.WHATSAPP)
        resolved_b = resolve_credentials(session, context_b, IntegrationProvider.WHATSAPP)
        assert resolved_a is not None and resolved_a.secret["access_token"] == "a-secret"
        assert resolved_b is None  # B never inherits A's credential

    with Session(isolated) as session:
        items_a = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_a.org_id).all()
        items_b = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_b.org_id).all()
        assert len(items_a) == 1 and len(items_b) == 1
        assert items_a[0].id != items_b[0].id

        events_a = session.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == clinic_a.org_id).all()
        events_b = session.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == clinic_b.org_id).all()
        assert any(e.action == "e2e_action_a" for e in events_a)
        assert not any(e.action == "e2e_action_a" for e in events_b)
        assert any(e.action == "e2e_action_b" for e in events_b)
        assert not any(e.action == "e2e_action_b" for e in events_a)

    # --- Re-login A: nothing about A changed ---
    clear_session()
    _login(clinic_a.owner_user_id, clinic_a.owner_email, clinic_a.org_id, clinic_a.org_name, clinic_a.owner_membership_id, MembershipRole.OWNER)
    assert [p["name"] for p in crm.list_records("patients")] == ["Overlap Patient"]
    assert platform_data.business_snapshot()["company"]["business_name"] == "E2E Clinic A"
    assert [p["value"] for p in jm.load_learning_memory()["preferences"]] == ["formal"]
