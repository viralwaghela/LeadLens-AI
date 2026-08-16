"""Independent adversarial audit tests for V2 Phase 6.1.1 (tenant-scoped
execution-preparation dedup fix). Written separately from
tests/test_phase6_1_audit.py and tests/test_phase6_1_execution_hardening.py
as a third, independently-designed adversarial pass. import _bootstrap
first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

import core.memory as business_memory
import services.credential_encryption as ce
import services.integration_credentials as ic
import services.integration_manager_v21 as mgr
import services.tenant_operational_sync as tos
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.integration import IntegrationProvider
from core.db.session import make_engine
from core.identity import organization_service
from core.identity.tenant_context import ActorType, TenantContext

ORG_SLUG = "phase6-1-1-audit-clinic"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr(tos, "_ENGINE", engine)
    monkeypatch.setattr(tos, "TENANT_CONTEXT_ENABLED", True)
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", ORG_SLUG)
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY_VERSION", raising=False)
    monkeypatch.delenv("LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED", raising=False)
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", False)
    ce._fernet_for_version.cache_clear()
    yield engine
    engine.dispose()
    ce._fernet_for_version.cache_clear()


def _context(org_id: int) -> TenantContext:
    return TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM, source="audit611")


# ---------------------------------------------------------------------------
# Item 5: even a DIRECTLY-INJECTED foreign-org row sharing the exact same
# fingerprint string cannot be returned to a different organization — the
# organization_id filter is independent of fingerprint content matching
# (true defense-in-depth, not merely "the hash makes collision improbable").
# ---------------------------------------------------------------------------

def test_direct_fingerprint_collision_with_foreign_org_row_never_returned(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Collision A", slug="collision-a")
        org_b = organization_service.create_organization(session, name="Collision B", slug="collision-b")
        session.commit()
        context_a, org_b_id = _context(org_a.id), org_b.id

    payload = {"to": "1", "body": "collision test"}
    # Compute exactly what Org A's real fingerprint WILL be for this
    # content, then plant a row under Org B's organization_id carrying
    # that identical fingerprint string, in "Awaiting approval" state —
    # simulating either a contrived attack or a hash-collision scenario.
    real_fingerprint = mgr._fingerprint(context_a.organization_id, "whatsapp", "send_text", payload)
    planted = {
        "id": "EXEC-PLANTED-FOREIGN",
        "provider": "whatsapp",
        "action": "send_text",
        "payload": payload,
        "title": "Planted foreign row",
        "fingerprint": real_fingerprint,
        "organization_id": org_b_id,  # belongs to B, not A
        "status": "Awaiting approval",
        "approval_id": "",
        "approval_status": "Pending",
        "created_at": "2026-01-01T00:00:00",
        "approved_at": "",
        "executed_at": "",
        "result": None,
    }
    rows = mgr._load()
    rows.append(planted)
    mgr._save(rows)

    result = mgr.prepare_execution("whatsapp", "send_text", payload, tenant_context=context_a)
    assert result["id"] != "EXEC-PLANTED-FOREIGN"
    assert result["organization_id"] == context_a.organization_id


# ---------------------------------------------------------------------------
# Item 7 (variant): a legacy row that DOES have organization_id set, but
# to a DIFFERENT organization than the caller, must not satisfy dedup
# either — the ownership filter, not merely "presence of the field," is
# what matters. (Distinct from the "missing field entirely" case already
# covered elsewhere.)
# ---------------------------------------------------------------------------

def test_existing_item_lookup_strictly_requires_organization_match(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Strict A", slug="strict-a")
        org_b = organization_service.create_organization(session, name="Strict B", slug="strict-b")
        session.commit()
        context_a, context_b = _context(org_a.id), _context(org_b.id)

    payload = {"to": "1", "body": "strict ownership test"}
    item_b = mgr.prepare_execution("whatsapp", "send_text", payload, tenant_context=context_b)
    item_a = mgr.prepare_execution("whatsapp", "send_text", payload, tenant_context=context_a)
    assert item_a["id"] != item_b["id"]

    # Re-preparing under B again (same org, same content, still pending)
    # DOES dedupe as before.
    item_b_again = mgr.prepare_execution("whatsapp", "send_text", payload, tenant_context=context_b)
    assert item_b_again["id"] == item_b["id"]


# ---------------------------------------------------------------------------
# Item 6: returned approval always belongs to the SAME organization as the
# calling context, verified via the relational shadow copy too (not just
# the legacy dict).
# ---------------------------------------------------------------------------

def test_shadow_synced_approval_matches_calling_organization(isolated) -> None:
    from core.db.models.operations import Approval

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Shadow Approval Org", slug="shadow-approval-org")
        session.commit()
        context = _context(org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)

    with Session(isolated) as session:
        approval_row = session.query(Approval).filter(Approval.external_id == item["approval_id"]).one()
        assert approval_row.organization_id == org.id


# ---------------------------------------------------------------------------
# Item 10: downstream tenant-scoped idempotency (scheduler ledger) is
# unaffected by this fix — three organizations, same check+item_key.
# ---------------------------------------------------------------------------

def test_scheduler_idempotency_still_org_scoped_after_dedup_fix(isolated, monkeypatch) -> None:
    import scheduler.run_scheduled_checks as sched
    from core.db.models.operations import SchedulerAlertLedgerEntry

    for slug in ("idem-org-a", "idem-org-b", "idem-org-c"):
        monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", slug)
        sched._mark_flagged("low_booking_alert", "SAME-KEY", f"note for {slug}")

    with Session(isolated) as session:
        entries = session.query(SchedulerAlertLedgerEntry).all()
    assert len(entries) == 3  # one per organization, same check_name+item_key


# ---------------------------------------------------------------------------
# Item 9: Phase 6.1's execute_item() hardening (fail-closed on missing/
# inactive/nonexistent org) remains intact after this fix.
# ---------------------------------------------------------------------------

def test_execute_item_still_fails_closed_on_missing_organization_id(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Still Hardened Org", slug="still-hardened-org")
        session.commit()
        context = _context(org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)
    mgr.decide_item(item["id"], "Approved")

    rows = mgr._load()
    for row in rows:
        if row["id"] == item["id"]:
            row.pop("organization_id", None)
    mgr._save(rows)

    result = mgr.execute_item(item["id"])
    assert result["success"] is False
    assert result["status"] == "blocked"
