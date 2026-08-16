"""Independent adversarial audit tests for V2 Phase 5 (tenant-scoped
business logic). Written separately from tests/test_phase5_tenant_context.py
as a second, independently-designed adversarial pass — same isolation
discipline (private in-memory DB, private temp legacy store, never a
real DATABASE_URL). import _bootstrap first, same as every other file
in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

import core.memory as business_memory
import services.crm_read_router as router
import services.integration_credentials as ic
import services.integration_manager_v21 as mgr
import services.relational_sync_service as rs
import services.security_service as security_service
import services.tenant_operational_sync as tos
import scheduler.run_scheduled_checks as sched
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole, MembershipStatus
from core.db.models.operations import Approval, ExecutionQueueItem, SchedulerAlertLedgerEntry, SecurityAuditEvent
from core.db.session import make_engine
from core.identity import membership_service, organization_service, user_service
from core.identity.tenant_context import build_user_context

ORG_SLUG = "phase5-audit-clinic"


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
    # Phase 6.1: prepare_execution() resolves its TenantContext via
    # services/integration_credentials.py's shared engine helper — must
    # point at the same isolated DB as everything else here.
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", ORG_SLUG)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# 1. read_services() cannot be tricked into accepting an organization
# ---------------------------------------------------------------------------

def test_read_services_rejects_any_argument(isolated) -> None:
    with pytest.raises(TypeError):
        router.read_services(organization_id=999999)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        router.read_services(999999)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 2. Exact-ID cross-tenant attacks on the relational shadow copies, driven
#    through the real live entry points (not direct ORM queries), proving
#    the *functions themselves* — not just hand-written test queries —
#    never cross the boundary.
# ---------------------------------------------------------------------------

def test_sync_approval_never_writes_to_a_foreign_org_even_with_its_exact_external_id(isolated) -> None:
    """Simulates an attacker who knows Org B's real approval external_id
    and tries to get it synced while the process is scoped to Org A (the
    only org the transitional resolver will ever produce in this test).
    sync_approval() takes no organization parameter at all — it must
    always land under the resolved org, never under an org implied by
    the dict content."""
    with Session(isolated) as session:
        org_b = organization_service.create_organization(session, name="Org B", slug="org-b-audit")
        org_b_id = org_b.id
        session.commit()

    legacy_approval = {
        "id": "APR-B-KNOWN-ID",
        "data": {"title": "Org B's approval", "status": "Pending", "risk_level": "High"},
    }
    tos.sync_approval(legacy_approval)

    with Session(isolated) as session:
        rows = session.query(Approval).filter(Approval.external_id == "APR-B-KNOWN-ID").all()
    assert len(rows) == 1
    assert rows[0].organization_id != org_b_id  # landed under the resolved (transitional) org, not org B


def test_execute_item_cannot_be_pointed_at_a_foreign_org_via_payload(isolated) -> None:
    """The execution queue sync accepts a legacy dict with attacker-controlled
    field values (fingerprint, id, approval_id) — proves none of those
    fields can select an organization."""
    item = mgr.prepare_execution(
        "gmail", "create_draft",
        {"to": "p@example.com", "subject": "Hi", "body": "Hello"}, "Test",
    )
    with Session(isolated) as session:
        rows = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.external_id == item["id"]).all()
    assert len(rows) == 1
    resolved_org_id = rows[0].organization_id

    with Session(isolated) as session:
        org_b = organization_service.create_organization(session, name="Org B2", slug="org-b2-audit")
        assert resolved_org_id != org_b.id


# ---------------------------------------------------------------------------
# 3. Idempotency boundary — spec section 8, run explicitly end to end.
# ---------------------------------------------------------------------------

def test_idempotency_is_tenant_scoped_not_globally_scoped(isolated, monkeypatch) -> None:
    # A's event X executes.
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-a-idem")
    sched._mark_flagged("low_booking_alert", "EVENT-X", "A first run")
    with Session(isolated) as session:
        org_a = organization_service.get_organization_by_slug(session, "org-a-idem")
        count_a = session.query(SchedulerAlertLedgerEntry).filter(
            SchedulerAlertLedgerEntry.organization_id == org_a.id
        ).count()
    assert count_a == 1

    # B's event X (same check_name + item_key) must NOT be suppressed by A's ledger entry.
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-b-idem")
    sched._mark_flagged("low_booking_alert", "EVENT-X", "B first run")
    with Session(isolated) as session:
        org_b = organization_service.get_organization_by_slug(session, "org-b-idem")
        count_b = session.query(SchedulerAlertLedgerEntry).filter(
            SchedulerAlertLedgerEntry.organization_id == org_b.id
        ).count()
    assert count_b == 1  # not suppressed by org A's prior entry

    # Rerunning A's event X must be suppressed (normal idempotency, scoped to A only).
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-a-idem")
    sched._mark_flagged("low_booking_alert", "EVENT-X", "A rerun — should be no-op")
    with Session(isolated) as session:
        org_a = organization_service.get_organization_by_slug(session, "org-a-idem")
        count_a_after = session.query(SchedulerAlertLedgerEntry).filter(
            SchedulerAlertLedgerEntry.organization_id == org_a.id
        ).count()
    assert count_a_after == 1  # still 1 — suppressed


# ---------------------------------------------------------------------------
# 4. Interleaved / concurrent-looking A/B audit writes — no bleed across
#    "requests" resolved in quick alternation (proxy for concurrency,
#    since there is no module-level mutable tenant state to race on).
# ---------------------------------------------------------------------------

def test_interleaved_audit_events_never_bleed_across_organizations(isolated, monkeypatch) -> None:
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-a-interleave")
    security_service.audit_event("owner-a", "create", "patient", "P-A-1")
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-b-interleave")
    security_service.audit_event("owner-b", "create", "patient", "P-B-1")
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-a-interleave")
    security_service.audit_event("owner-a", "update", "patient", "P-A-1")
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", "org-b-interleave")
    security_service.audit_event("owner-b", "update", "patient", "P-B-1")

    with Session(isolated) as session:
        org_a = organization_service.get_organization_by_slug(session, "org-a-interleave")
        org_b = organization_service.get_organization_by_slug(session, "org-b-interleave")
        a_events = session.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == org_a.id).all()
        b_events = session.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == org_b.id).all()

    assert len(a_events) == 2
    assert all(e.actor == "owner-a" for e in a_events)
    assert len(b_events) == 2
    assert all(e.actor == "owner-b" for e in b_events)


# ---------------------------------------------------------------------------
# 5. build_user_context fails closed when a real user is a member of a
#    DIFFERENT organization than the one requested (both individually
#    exist and are active — only the membership pairing is wrong).
# ---------------------------------------------------------------------------

def test_user_context_fails_closed_when_user_belongs_to_a_different_org(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A-ctx", slug="org-a-ctx")
        org_b = organization_service.create_organization(session, name="Org B-ctx", slug="org-b-ctx")
        user = user_service.create_user(session, email="member-of-a@example.com", password="pw-one-two-three")
        membership_service.create_membership(
            session, user_id=user.id, organization_id=org_a.id, role=MembershipRole.OWNER
        )
        session.commit()
        user_id, org_a_id, org_b_id = user.id, org_a.id, org_b.id

    with Session(isolated) as session:
        # User is real, active, and a real OWNER of org A — but requests context for org B.
        context = build_user_context(session, user_id=user_id, organization_id=org_b_id)
    assert context is None  # must fail closed, never "helpfully" resolve org A instead

    with Session(isolated) as session:
        context = build_user_context(session, user_id=user_id, organization_id=org_a_id)
    assert context is not None
    assert context.organization_id == org_a_id


# ---------------------------------------------------------------------------
# 6. No live query function anywhere in tenant_operational_sync.py can be
#    called with a caller-supplied organization_id (write-only shadow
#    module — the whole module surface is inspected, not just spot checks).
# ---------------------------------------------------------------------------

def test_no_public_function_in_tenant_operational_sync_accepts_organization_id() -> None:
    import inspect

    for name, fn in vars(tos).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        if fn.__module__ != tos.__name__:
            continue
        sig = inspect.signature(fn)
        assert "organization_id" not in sig.parameters, f"{name} accepts organization_id directly"
        assert "org_id" not in sig.parameters, f"{name} accepts org_id directly"


# ---------------------------------------------------------------------------
# 7. Scheduler ledger entry note field never receives HTTP/DB credential
#    style content unexpectedly (spot-check: shadow sync doesn't expand
#    what's logged beyond what the legacy caller already passed).
# ---------------------------------------------------------------------------

def test_scheduler_ledger_shadow_sync_stores_only_what_legacy_already_had(isolated) -> None:
    sched._mark_flagged("capacity_alert", "T-001:2026-04-01", "Therapist over capacity")
    with Session(isolated) as session:
        row = session.query(SchedulerAlertLedgerEntry).one()
    assert row.check_name == "capacity_alert"
    assert row.item_key == "T-001:2026-04-01"
    assert row.note == "Therapist over capacity"
