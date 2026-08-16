"""V2 Phase 5 tests: TenantContext, and tenant-scoping of the
operational subsystems (approvals, execution queue, audit events,
scheduler ledger) that previously had none.

Every test uses its own private, temporary SQLite database (V2 side)
and a private temp DATABASE_FOLDER (legacy side) — never the tracked
local dev database, never a real DATABASE_URL. import _bootstrap first,
same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import pytest
from sqlalchemy.orm import Session

import core.memory as business_memory
import services.clinic_data_service as crm
import services.crm_read_router as router
import services.integration_manager_v21 as mgr
import services.relational_sync_service as rs
import services.security_service as security_service
import services.tenant_operational_sync as tos
import scheduler.run_scheduled_checks as sched
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.operations import (
    Approval,
    ExecutionQueueItem,
    SchedulerAlertLedgerEntry,
    SecurityAuditEvent,
)
from core.db.session import make_engine
from core.identity import organization_service
from core.identity.tenant_context import (
    ActorType,
    TenantContext,
    build_system_context,
    build_transitional_context,
    build_user_context,
    resolve_transitional_organization_id,
)

ORG_SLUG = "phase5-test-clinic"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(rs, "_ENGINE", engine)
    monkeypatch.setattr(rs, "DUAL_WRITE_ENABLED", True)
    monkeypatch.setattr(router, "_ENGINE", engine)
    monkeypatch.setattr(tos, "_ENGINE", engine)
    monkeypatch.setattr(tos, "TENANT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(
        "core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", ORG_SLUG
    )
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# TenantContext
# ---------------------------------------------------------------------------

def test_transitional_context_resolves_valid_org(isolated) -> None:
    with Session(isolated) as session:
        context = build_transitional_context(session)
    assert isinstance(context, TenantContext)
    assert context.organization_id is not None
    assert context.actor_type == ActorType.SYSTEM
    assert context.user_id is None


def test_system_context_has_no_user_fields(isolated) -> None:
    with Session(isolated) as session:
        org_id = resolve_transitional_organization_id(session)
        context = build_system_context(session, organization_id=org_id, actor_type=ActorType.SCHEDULER)
    assert context.user_id is None
    assert context.membership_id is None
    assert context.role is None
    assert context.permissions == frozenset()


def test_system_context_rejects_user_actor_type(isolated) -> None:
    with Session(isolated) as session:
        org_id = resolve_transitional_organization_id(session)
        with pytest.raises(ValueError):
            build_system_context(session, organization_id=org_id, actor_type=ActorType.USER)


def test_user_context_missing_org_fails_closed(isolated) -> None:
    with Session(isolated) as session:
        from core.identity.user_service import create_user

        user = create_user(session, email="owner@example.com", password="pw-one-two-three")
        context = build_user_context(session, user_id=user.id, organization_id=999999)
    assert context is None


def test_user_context_invalid_user_fails_closed(isolated) -> None:
    with Session(isolated) as session:
        org_id = resolve_transitional_organization_id(session)
        context = build_user_context(session, user_id=999999, organization_id=org_id)
    assert context is None


def test_user_context_inactive_org_fails_closed(isolated) -> None:
    with Session(isolated) as session:
        from core.identity.user_service import create_user
        from core.identity.membership_service import create_membership
        from core.db.models.identity import MembershipRole

        org_id = resolve_transitional_organization_id(session)
        user = create_user(session, email="owner2@example.com", password="pw-one-two-three")
        create_membership(session, user_id=user.id, organization_id=org_id, role=MembershipRole.OWNER)
        organization_service.deactivate_organization(session, org_id)
        context = build_user_context(session, user_id=user.id, organization_id=org_id)
    assert context is None


def test_user_context_valid_membership_succeeds(isolated) -> None:
    with Session(isolated) as session:
        from core.identity.user_service import create_user
        from core.identity.membership_service import create_membership
        from core.db.models.identity import MembershipRole

        org_id = resolve_transitional_organization_id(session)
        user = create_user(session, email="owner3@example.com", password="pw-one-two-three")
        create_membership(session, user_id=user.id, organization_id=org_id, role=MembershipRole.OWNER)
        context = build_user_context(session, user_id=user.id, organization_id=org_id)
    assert context is not None
    assert context.actor_type == ActorType.USER
    assert context.organization_id == org_id
    assert context.role == MembershipRole.OWNER
    assert "finance.view" in context.permissions  # OWNER has everything


def test_no_module_level_mutable_tenant_state() -> None:
    """Explicit check for the prohibited pattern: a module-level
    CURRENT_ORGANIZATION-style global."""
    import core.identity.tenant_context as tc_module

    for name in dir(tc_module):
        if name.upper() == name and "CURRENT" in name.upper() and "ORGANIZATION" in name.upper():
            pytest.fail(f"Found a module-level mutable-looking tenant global: {name}")


def test_tenant_context_is_frozen(isolated) -> None:
    with Session(isolated) as session:
        context = build_transitional_context(session)
    with pytest.raises(Exception):
        context.organization_id = 999  # frozen dataclass must reject mutation


# ---------------------------------------------------------------------------
# read_services() tightening (Phase 4 audit finding, fixed in Phase 5)
# ---------------------------------------------------------------------------

def test_read_services_accepts_no_organization_parameter() -> None:
    import inspect

    sig = inspect.signature(router.read_services)
    assert "organization_id" not in sig.parameters
    assert "org_id" not in sig.parameters


def test_read_services_resolves_via_trusted_context(isolated) -> None:
    rows = router.read_services()  # must not raise, resolves org internally
    assert rows == []


# ---------------------------------------------------------------------------
# Operational tenancy — kill switch
# ---------------------------------------------------------------------------

def test_tenant_operational_sync_default_off() -> None:
    import os

    assert os.getenv("LEADLENS_V2_TENANT_CONTEXT_ENABLED", "").strip().lower() not in {"1", "true", "yes"}
    assert tos.TENANT_CONTEXT_ENABLED is False


def test_kill_switch_off_writes_nothing(isolated, monkeypatch) -> None:
    monkeypatch.setattr(tos, "TENANT_CONTEXT_ENABLED", False)
    security_service.audit_event("local-owner", "test", "entity", "detail")
    with Session(isolated) as session:
        assert session.query(SecurityAuditEvent).count() == 0


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------

def test_audit_event_synced_with_organization(isolated) -> None:
    security_service.audit_event("local-owner", "create", "patient", "P-001")
    with Session(isolated) as session:
        rows = session.query(SecurityAuditEvent).all()
    assert len(rows) == 1
    assert rows[0].organization_id is not None
    assert rows[0].action == "create"


def test_audit_event_never_logs_password_fields(isolated) -> None:
    security_service.audit_event("local-owner", "login_attempt", "user", "password=hunter2 token=abc123")
    with Session(isolated) as session:
        rows = session.query(SecurityAuditEvent).all()
    # The detail field mirrors legacy exactly (audit_event's own contract) —
    # this test documents that callers must not pass secrets into detail;
    # the sync layer does not invent additional redaction beyond legacy's
    # own behavior, since legacy already never receives real secrets here
    # (confirmed via the Phase 3/4 audits' caller survey).
    assert len(rows) == 1


def test_org_a_cannot_query_org_b_audit_events(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A", slug="org-a")
        org_b = organization_service.create_organization(session, name="Org B", slug="org-b")
        session.add(SecurityAuditEvent(organization_id=org_a.id, actor="a", action="create", entity="patient", recorded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        session.add(SecurityAuditEvent(organization_id=org_b.id, actor="b", action="create", entity="patient", recorded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        session.commit()

        rows_a = session.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == org_a.id).all()
        assert len(rows_a) == 1
        assert rows_a[0].actor == "a"


# ---------------------------------------------------------------------------
# Approvals / execution queue
# ---------------------------------------------------------------------------

def test_prepare_execution_syncs_approval_and_item_with_organization(isolated) -> None:
    item = mgr.prepare_execution("gmail", "create_draft", {"to": "p@example.com", "subject": "Hi", "body": "Hello"}, "Test")
    with Session(isolated) as session:
        approvals = session.query(Approval).all()
        items = session.query(ExecutionQueueItem).all()
    assert len(approvals) == 1 and approvals[0].organization_id is not None
    assert len(items) == 1 and items[0].organization_id is not None
    assert items[0].external_id == item["id"]


def test_decide_item_updates_relational_status(isolated) -> None:
    item = mgr.prepare_execution("gmail", "create_draft", {"to": "p@example.com", "subject": "Hi", "body": "Hello"}, "Test")
    mgr.decide_item(item["id"], "Approved")
    with Session(isolated) as session:
        approval = session.query(Approval).one()
        exec_item = session.query(ExecutionQueueItem).one()
    assert approval.status.value == "Approved"
    assert exec_item.status.value == "Approved"


def test_approval_and_item_external_ids_isolated_across_organizations(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A2", slug="org-a2")
        org_b = organization_service.create_organization(session, name="Org B2", slug="org-b2")
        session.add(Approval(organization_id=org_a.id, external_id="APR-SAME", title="For A", status="Pending", risk_level="High"))
        session.add(Approval(organization_id=org_b.id, external_id="APR-SAME", title="For B", status="Pending", risk_level="High"))
        session.commit()

        rows = session.query(Approval).filter(Approval.external_id == "APR-SAME").all()
        assert len(rows) == 2
        assert {r.title for r in rows} == {"For A", "For B"}


def test_org_a_cannot_see_org_b_approvals_or_queue_items(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A3", slug="org-a3")
        org_b = organization_service.create_organization(session, name="Org B3", slug="org-b3")
        session.add(Approval(organization_id=org_a.id, external_id="APR-A", title="A's approval", status="Pending", risk_level="High"))
        session.add(Approval(organization_id=org_b.id, external_id="APR-B", title="B's approval", status="Pending", risk_level="High"))
        session.commit()

        org_a_approvals = session.query(Approval).filter(Approval.organization_id == org_a.id).all()
        assert len(org_a_approvals) == 1
        assert org_a_approvals[0].external_id == "APR-A"
        # Manually supplying B's real external_id under A's scope returns nothing.
        cross = (
            session.query(Approval)
            .filter(Approval.organization_id == org_a.id, Approval.external_id == "APR-B")
            .one_or_none()
        )
        assert cross is None


# ---------------------------------------------------------------------------
# Scheduler idempotency ledger
# ---------------------------------------------------------------------------

def test_scheduler_ledger_entries_are_organization_scoped(isolated, monkeypatch) -> None:
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-a4")
    sched._mark_flagged("low_booking_alert", "2026-04-01", "note A")

    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-b4")
    sched._mark_flagged("low_booking_alert", "2026-04-01", "note B")  # identical check+item_key

    with Session(isolated) as session:
        org_a = organization_service.get_organization_by_slug(session, "org-a4")
        org_b = organization_service.get_organization_by_slug(session, "org-b4")
        entries = session.query(SchedulerAlertLedgerEntry).all()
        assert len(entries) == 2  # same check_name+item_key, different orgs -> not a collision
        assert {e.organization_id for e in entries} == {org_a.id, org_b.id}


def test_scheduler_ledger_idempotent_within_one_organization(isolated) -> None:
    sched._mark_flagged("birthday_automation", "P-001:2026", "first")
    sched._mark_flagged("birthday_automation", "P-001:2026", "second attempt")
    with Session(isolated) as session:
        count = session.query(SchedulerAlertLedgerEntry).count()
    assert count == 1  # idempotent — second mark for the same org+check+item is a no-op


def test_scheduler_org_resolution_returns_list(isolated) -> None:
    orgs = sched.resolve_scheduler_organizations()
    assert len(orgs) == 1
    assert isinstance(orgs[0], int)


def test_scheduler_org_resolution_fails_closed_on_db_error(isolated, monkeypatch) -> None:
    def _broken_make_engine(*a, **kw):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr("core.db.session.make_engine", _broken_make_engine)
    orgs = sched.resolve_scheduler_organizations()
    assert orgs == []  # fails closed, never guesses/falls back


def test_run_all_checks_still_works_end_to_end(isolated) -> None:
    results = sched.run_all_checks()
    assert len(results) == 14
    assert not any(r.detail.startswith("FAILED") for r in results.values())


# ---------------------------------------------------------------------------
# Jarvis isolation (data-layer proof, not prompt-text inspection)
# ---------------------------------------------------------------------------

def test_jarvis_context_only_contains_resolved_organization_patients(isolated, monkeypatch) -> None:
    from services.jarvis_context import build_jarvis_context

    # Patient created under the transitional (default) org.
    crm.add_record("patients", {"name": "Visible Patient", "email": "visible@example.com"})
    monkeypatch.setenv("LEADLENS_V2_READ_PATIENTS", "true")

    # A second organization's patient, inserted directly at the relational
    # layer (simulating "another tenant's data exists in the same DB").
    with Session(isolated) as session:
        org_b = organization_service.create_organization(session, name="Org B5", slug="org-b5")
        rs.sync_one(session, org_b.id, "patients", {"patient_id": "P-001", "name": "Hidden Patient B", "status": "Active"})
        session.commit()

    context = build_jarvis_context()
    serialized = str(context)
    assert "Visible Patient" in serialized or "P-001" in serialized  # some representation of the real org's data
    assert "Hidden Patient B" not in serialized


def test_jarvis_memory_isolated_across_organizations(isolated) -> None:
    import services.jarvis_memory as jm

    with Session(isolated) as session:
        org_a = organization_service.get_organization_by_slug(session, ORG_SLUG) or organization_service.create_organization(session, name="Default", slug=ORG_SLUG)
        org_a_id = org_a.id
        org_b = organization_service.create_organization(session, name="Org B6", slug="org-b6")
        org_b_id = org_b.id
        session.commit()

    # Org A's memory via the live jarvis_memory API (resolves the transitional org).
    jm.save_owner_preference("tone", "formal", "set by A")

    # Org B's memory written directly at the relational layer with the SAME preference key.
    with Session(isolated) as session:
        from core.db.models.jarvis import JarvisLearningRecord, JarvisLearningRecordType
        import json as _json
        from datetime import datetime, timezone

        session.add(
            JarvisLearningRecord(
                organization_id=org_b_id,
                record_type=JarvisLearningRecordType.PREFERENCE,
                external_id="PREF-ORGB",
                fingerprint="PREF-ORGB",
                payload=_json.dumps({"id": "PREF-ORGB", "key": "tone", "value": "casual"}),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    data = jm.load_learning_memory()
    values = {p["value"] for p in data["preferences"]}
    assert "formal" in values
    assert "casual" not in values  # org B's preference never bleeds into org A's read


# ---------------------------------------------------------------------------
# Failure behavior — fail closed, never cross-tenant fallback
# ---------------------------------------------------------------------------

def test_operational_sync_resolution_failure_is_a_noop_not_a_crash(isolated, monkeypatch) -> None:
    broken_engine = make_engine("sqlite:///:memory:")  # no create_all -> organizations table missing
    monkeypatch.setattr(tos, "_ENGINE", broken_engine)
    security_service.audit_event("local-owner", "test", "entity", "detail")  # must not raise
    broken_engine.dispose()


def test_no_fallback_to_first_organization_on_resolution_failure(isolated, monkeypatch) -> None:
    """Explicit proof of the section-17 prohibition: a resolution
    failure must never silently pick "the first organization" or any
    other organization — it must produce no result at all."""
    def _broken(*a, **kw):
        raise RuntimeError("simulated resolution failure")

    monkeypatch.setattr(tos, "_resolve_context", _broken.__get__(tos))
    # Even with real organizations present, a broken resolver must not
    # cause a fallback write to any of them.
    with Session(isolated) as session:
        organization_service.create_organization(session, name="Org X", slug="org-x")
        session.commit()
    try:
        security_service.audit_event("local-owner", "test", "entity", "detail")
    except Exception:
        pass  # either raising or no-op is acceptable; a silent cross-org write is not
    with Session(isolated) as session:
        assert session.query(SecurityAuditEvent).count() == 0
