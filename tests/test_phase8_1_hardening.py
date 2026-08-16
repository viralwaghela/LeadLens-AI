"""V2 Phase 8.1 tests: multi-tenant audit + scheduler execution hardening.

Covers the two confirmed defects from the independent Phase 8 audit:

    1. audit_event()/audit_rows() wrote/read the single legacy global
       security_audit_log section regardless of organization — a real
       cross-tenant audit-log leak. Fixed by making audit_rows()
       organization-scoped (relational SecurityAuditEvent) when
       LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED is on.
    2. scheduler/run_scheduled_checks.py's 14 check functions resolved
       tenant context implicitly rather than receiving the organization
       currently being enumerated. Fixed by threading an explicit
       `context: TenantContext | None` parameter through every check
       function and its CRM/action calls.

Uses the same Fixture/isolated-engine/bare-mode-session-state pattern as
tests/test_phase8_saas_onboarding.py. import _bootstrap first, same as
every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
import streamlit as st
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

import core.auth as auth
import core.memory as business_memory
import scheduler.run_scheduled_checks as sched
import services.clinic_data_service as crm
import services.crm_read_router as crm_router
import services.integration_credentials as ic
import services.jarvis_memory as jm
import services.platform_data as platform_data
import services.relational_sync_service as rs
import services.security_service as sec
import services.tenant_operational_sync as tos
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole
from core.db.models.operations import Approval, ExecutionQueueItem, SecurityAuditEvent
from core.db.session import make_engine
from core.identity import membership_service, organization_service, user_service
from core.identity.organization_profile_service import set_automations_enabled
from core.identity.permissions import permissions_for_role
from core.identity.session import AuthenticatedSession, clear_session, store_session
from core.identity.tenant_context import ActorType, TenantContext
from services.authorization_guard import PermissionDenied
from services.security_service import audit_event, audit_rows

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
    monkeypatch.setattr(tos, "_ENGINE", engine)
    monkeypatch.setattr(sec, "_ENGINE", engine)
    monkeypatch.setenv("LEADLENS_V2_AUTH_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_TENANT_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED", "true")
    monkeypatch.setattr(crm_router, "TENANT_AUTHORITATIVE_ENABLED", True)
    monkeypatch.setattr(tos, "TENANT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(sec, "AUDIT_TENANT_AUTHORITATIVE_ENABLED", True)

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
    owner_user_id: int
    owner_email: str
    owner_membership_id: int


def _provision(session, *, slug: str, name: str, owner_email: str, role=MembershipRole.OWNER) -> Clinic:
    org = organization_service.create_organization(session, name=name, slug=slug)
    user = user_service.create_user(session, email=owner_email, password=PASSWORD)
    membership = membership_service.create_membership(session, user_id=user.id, organization_id=org.id, role=role)
    session.commit()
    return Clinic(
        org_id=org.id, org_name=org.name,
        owner_user_id=user.id, owner_email=user.email, owner_membership_id=membership.id,
    )


def _login(clinic: Clinic, role=MembershipRole.OWNER) -> None:
    store_session(
        AuthenticatedSession(
            user_id=clinic.owner_user_id, email=clinic.owner_email, organization_id=clinic.org_id,
            organization_name=clinic.org_name, membership_id=clinic.owner_membership_id, role=role,
            permissions=permissions_for_role(role),
        )
    )


# ---------------------------------------------------------------------------
# 1. AUDIT — organization-scoped reads
# ---------------------------------------------------------------------------

def test_audit_a_event_visible_to_a_only(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="aud-a", name="Aud A", owner_email="a@aud.example")
        clinic_b = _provision(session, slug="aud-b", name="Aud B", owner_email="b@aud.example")

    _login(clinic_a)
    audit_event(clinic_a.owner_email, "OVERLAP_ACTION", "patient", "A detail")
    rows_a = audit_rows()
    assert any(r["action"] == "OVERLAP_ACTION" and r["detail"] == "A detail" for r in rows_a)

    clear_session()
    _login(clinic_b)
    rows_b = audit_rows()
    assert not any(r.get("detail") == "A detail" for r in rows_b)


def test_audit_b_event_visible_to_b_only_overlapping_action_name(isolated) -> None:
    """Deliberately overlapping action/entity names between A and B —
    isolation must hold on content (detail), not just naming."""
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="aud2-a", name="Aud2 A", owner_email="a@aud2.example")
        clinic_b = _provision(session, slug="aud2-b", name="Aud2 B", owner_email="b@aud2.example")

    _login(clinic_a)
    audit_event(clinic_a.owner_email, "OVERLAP_ACTION", "patient", "A payload")

    clear_session()
    _login(clinic_b)
    audit_event(clinic_b.owner_email, "OVERLAP_ACTION", "patient", "B payload")
    rows_b = audit_rows()
    b_details = [r["detail"] for r in rows_b if r["action"] == "OVERLAP_ACTION"]
    assert b_details == ["B payload"]


def test_audit_a_cannot_see_b_reverse_direction(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="aud3-a", name="Aud3 A", owner_email="a@aud3.example")
        clinic_b = _provision(session, slug="aud3-b", name="Aud3 B", owner_email="b@aud3.example")

    _login(clinic_b)
    audit_event(clinic_b.owner_email, "B_ONLY_EVENT", "test", "")

    clear_session()
    _login(clinic_a)
    audit_event(clinic_a.owner_email, "A_ONLY_EVENT", "test", "")
    rows_a = audit_rows()
    assert any(r["action"] == "A_ONLY_EVENT" for r in rows_a)
    assert not any(r["action"] == "B_ONLY_EVENT" for r in rows_a)


def test_audit_view_permission_still_enforced(isolated) -> None:
    with Session(isolated) as session:
        clinic = _provision(session, slug="aud-rbac", name="Aud RBAC", owner_email="owner@aud-rbac.example")
        viewer_user = user_service.create_user(session, email="viewer@aud-rbac.example", password=PASSWORD)
        viewer_membership = membership_service.create_membership(
            session, user_id=viewer_user.id, organization_id=clinic.org_id, role=MembershipRole.VIEWER,
        )
        session.commit()
        viewer_user_id, viewer_email, viewer_membership_id = viewer_user.id, viewer_user.email, viewer_membership.id

    store_session(
        AuthenticatedSession(
            user_id=viewer_user_id, email=viewer_email, organization_id=clinic.org_id,
            organization_name=clinic.org_name, membership_id=viewer_membership_id, role=MembershipRole.VIEWER,
            permissions=permissions_for_role(MembershipRole.VIEWER),
        )
    )
    with pytest.raises(PermissionDenied):
        audit_rows()


def test_audit_legacy_mode_compatibility_when_flag_off(isolated, monkeypatch) -> None:
    """With the new flag off, audit_rows() must behave exactly as before
    Phase 8.1 — reading the single legacy global section — for scripts,
    legacy deployments, and any call with no live session."""
    monkeypatch.setattr(sec, "AUDIT_TENANT_AUTHORITATIVE_ENABLED", False)
    audit_event("script-actor", "LEGACY_EVENT", "test", "legacy detail")
    rows = audit_rows()
    assert any(r["action"] == "LEGACY_EVENT" for r in rows)


# ---------------------------------------------------------------------------
# 2. SCHEDULER — explicit organization context
# ---------------------------------------------------------------------------

def _context_for(clinic: Clinic) -> TenantContext:
    return TenantContext(organization_id=clinic.org_id, actor_type=ActorType.SCHEDULER)


def test_scheduler_check_a_sees_only_a_overlapping_ids(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="sched8-a", name="Sched8 A", owner_email="a@sched8.example")
        clinic_b = _provision(session, slug="sched8-b", name="Sched8 B", owner_email="b@sched8.example")

    crm.add_record("therapists", {"name": "Dr. A", "weekly_capacity": 10}, organization_id=clinic_a.org_id)
    crm.add_record("therapists", {"name": "Dr. B", "weekly_capacity": 10}, organization_id=clinic_b.org_id)
    # deliberately overlapping external ids ("T-001" in both orgs)

    result_a = sched.capacity_alert(_context_for(clinic_a))
    result_b = sched.capacity_alert(_context_for(clinic_b))
    assert "no therapists over capacity" in result_a.detail
    assert "no therapists over capacity" in result_b.detail  # both empty, no cross-contamination either way


def test_scheduler_birthday_automation_a_b_isolated_overlapping_dob(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="bday-a", name="Bday A", owner_email="a@bday.example")
        clinic_b = _provision(session, slug="bday-b", name="Bday B", owner_email="b@bday.example")

    import datetime
    today = datetime.date.today()
    dob = today.replace(year=today.year - 30).isoformat()

    crm.add_record(
        "patients",
        {"name": "Alice", "date_of_birth": dob, "consent_to_contact": True, "phone": "1111111111"},
        organization_id=clinic_a.org_id,
    )
    crm.add_record(
        "patients",
        {"name": "Bob", "date_of_birth": dob, "consent_to_contact": True, "phone": "2222222222"},
        organization_id=clinic_b.org_id,
    )

    result_a = sched.birthday_automation(_context_for(clinic_a))
    assert result_a.approvals_queued == 1

    result_b = sched.birthday_automation(_context_for(clinic_b))
    assert result_b.approvals_queued == 1  # B's own birthday patient queued independently, not suppressed by A's run

    with Session(isolated) as session:
        items_a = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_a.org_id).all()
        items_b = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_b.org_id).all()
        assert len(items_a) == 1
        assert len(items_b) == 1
        assert items_a[0].id != items_b[0].id


def test_scheduler_a_to_b_to_a_interleaving_no_bleed(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="inter-sched-a", name="Inter A", owner_email="a@inter-sched.example")
        clinic_b = _provision(session, slug="inter-sched-b", name="Inter B", owner_email="b@inter-sched.example")

    crm.add_record("leads", {"name": "Lead A", "source": "Website"}, organization_id=clinic_a.org_id)

    r1 = sched.lead_qualification_alert(_context_for(clinic_a))
    r2 = sched.lead_qualification_alert(_context_for(clinic_b))
    r3 = sched.lead_qualification_alert(_context_for(clinic_a))
    assert "1 open lead" in r1.detail
    assert "no open leads" in r2.detail
    assert "1 open lead" in r3.detail
    assert r3.skipped_duplicate == 1  # A's own alert already flagged, not because of B's run


def test_scheduler_disabled_org_skipped_from_enumeration(isolated, monkeypatch) -> None:
    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    import core.db.session as db_session_mod
    monkeypatch.setattr(db_session_mod, "make_engine", lambda *a, **k: isolated)

    with Session(isolated) as session:
        clinic = _provision(session, slug="sched-disabled", name="Sched Disabled", owner_email="owner@sched-disabled.example")

    with Session(isolated) as session:
        set_automations_enabled(session, clinic.org_id, True)
        organization_service.deactivate_organization(session, clinic.org_id)
        session.commit()

    assert clinic.org_id not in sched.resolve_scheduler_organizations()


def test_scheduler_automations_disabled_org_skipped(isolated, monkeypatch) -> None:
    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    import core.db.session as db_session_mod
    monkeypatch.setattr(db_session_mod, "make_engine", lambda *a, **k: isolated)

    with Session(isolated) as session:
        clinic = _provision(session, slug="sched-automations-off", name="Automations Off", owner_email="owner@automations-off.example")

    with Session(isolated) as session:
        set_automations_enabled(session, clinic.org_id, False)
        session.commit()

    assert clinic.org_id not in sched.resolve_scheduler_organizations()


def test_scheduler_idempotency_tenant_scoped(isolated) -> None:
    """Same idempotency item_key under A and B remains independent; A's
    rerun of the same key is suppressed, B's own first run is not."""
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="idem-a", name="Idem A", owner_email="a@idem.example")
        clinic_b = _provision(session, slug="idem-b", name="Idem B", owner_email="b@idem.example")

    context_a = _context_for(clinic_a)
    context_b = _context_for(clinic_b)

    first = sched.raise_owner_alert("idem_check", "SAME-KEY", title="t", message="m", context=context_a)
    assert first is True
    second_same_org = sched.raise_owner_alert("idem_check", "SAME-KEY", title="t", message="m", context=context_a)
    assert second_same_org is False  # suppressed for A's rerun

    first_for_b = sched.raise_owner_alert("idem_check", "SAME-KEY", title="t", message="m", context=context_b)
    assert first_for_b is True  # B's own first run with the identical key is NOT suppressed by A's


def test_scheduler_run_all_checks_multi_org_generates_org_scoped_items(isolated, monkeypatch) -> None:
    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    import core.db.session as db_session_mod
    monkeypatch.setattr(db_session_mod, "make_engine", lambda *a, **k: isolated)

    with Session(isolated) as session:
        clinic_a = _provision(session, slug="runall-a", name="Runall A", owner_email="a@runall.example")
        clinic_b = _provision(session, slug="runall-b", name="Runall B", owner_email="b@runall.example")

    with Session(isolated) as session:
        set_automations_enabled(session, clinic_a.org_id, True)
        set_automations_enabled(session, clinic_b.org_id, True)
        session.commit()

    import datetime
    today = datetime.date.today()
    dob = today.replace(year=today.year - 25).isoformat()
    crm.add_record(
        "patients", {"name": "Carol", "date_of_birth": dob, "consent_to_contact": True, "phone": "3333333333"},
        organization_id=clinic_a.org_id,
    )

    original_checks = list(sched.CHECKS)
    try:
        sched.CHECKS.clear()
        sched.CHECKS.append(sched.birthday_automation)
        results = sched.run_all_checks()
    finally:
        sched.CHECKS.clear()
        sched.CHECKS.extend(original_checks)

    assert results["birthday_automation"].approvals_queued == 1  # only A's patient, summed across both orgs

    with Session(isolated) as session:
        items_a = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_a.org_id).all()
        items_b = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.organization_id == clinic_b.org_id).all()
        assert len(items_a) == 1
        assert len(items_b) == 0  # B had no birthday patient — no item fabricated for B


def test_scheduler_single_org_legacy_mode_unaffected(isolated, monkeypatch) -> None:
    """With LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED off (the default),
    run_all_checks() must call every check with context=None exactly as
    before Phase 8.1 — no behavior change for existing deployments."""
    monkeypatch.delenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", raising=False)
    called_with = []
    original_checks = list(sched.CHECKS)
    try:
        sched.CHECKS.clear()

        @sched.check
        def probe(context=None):
            called_with.append(context)
            return sched.CheckResult()

        sched.run_all_checks()
    finally:
        sched.CHECKS.clear()
        sched.CHECKS.extend(original_checks)
    assert called_with == [None]


# ---------------------------------------------------------------------------
# 3. MARKETING SITE GUARD
# ---------------------------------------------------------------------------

def test_marketing_site_rejects_ambiguous_multi_org_database() -> None:
    import importlib.util
    import sys as _sys
    from pathlib import Path

    lead_path = Path(__file__).resolve().parents[1] / "marketing-site" / "api" / "lead.py"
    spec = importlib.util.spec_from_file_location("marketing_lead_module", lead_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchone.side_effect = [("organizations",), (2,)]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with pytest.raises(module.AmbiguousMultiOrgDatabaseError):
        module._reject_if_ambiguous_multi_org(fake_conn)


def test_marketing_site_allows_single_org_database() -> None:
    import importlib.util
    from pathlib import Path

    lead_path = Path(__file__).resolve().parents[1] / "marketing-site" / "api" / "lead.py"
    spec = importlib.util.spec_from_file_location("marketing_lead_module_single", lead_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchone.side_effect = [("organizations",), (1,)]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    module._reject_if_ambiguous_multi_org(fake_conn)  # must not raise


# ---------------------------------------------------------------------------
# 4. READINESS VERIFIER
# ---------------------------------------------------------------------------

def test_readiness_verifier_fails_when_audit_global_with_multiple_orgs(monkeypatch) -> None:
    import scripts.verify_multi_org_readiness as verifier

    monkeypatch.delenv("LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED", raising=False)
    monkeypatch.delenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", raising=False)
    findings = verifier.multi_org_readiness_gate(organizations_count=2)
    levels = [level for level, _ in findings]
    assert "FAIL" in levels


def test_readiness_verifier_passes_when_properly_configured(monkeypatch) -> None:
    import scripts.verify_multi_org_readiness as verifier

    monkeypatch.setenv("LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    findings = verifier.multi_org_readiness_gate(organizations_count=2)
    levels = [level for level, _ in findings]
    assert "FAIL" not in levels
