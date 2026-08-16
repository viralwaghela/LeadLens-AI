"""V2 Phase 6.1 tests: multi-organization execution-queue hardening.

Covers the three Phase 6 audit findings this phase fixes:
  1. execute_item() now derives its TenantContext strictly from the
     queue item's own stamped organization_id, never the transitional
     default.
  2. configure_integration() no longer marks a fresh, secret-less
     integration ACTIVE.
  3. credential_encryption.redact() is documented as an unwired,
     opt-in utility (no live boundary needed one) — covered here by
     confirming fake secrets never appear in logs/audit/exceptions
     through the actual execution path, which is the real invariant.

Every test uses its own private, temporary SQLite database (V2 side)
and a private temp DATABASE_FOLDER (legacy side) — never the tracked
local dev database, never a real DATABASE_URL, never a real provider
API call (requests.post is monkeypatched). All secrets used here are
obviously-fake test values. import _bootstrap first, same as every
other file in tests/.
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
from core.db.models.integration import IntegrationProvider, IntegrationStatus
from core.db.models.operations import Approval, ExecutionQueueItem
from core.db.session import make_engine
from core.identity import organization_service
from core.identity.tenant_context import ActorType, TenantContext

ORG_SLUG = "phase6-1-test-clinic"

FAKE_A = {"access_token": "fake-6.1-token-AAAA", "phone_number_id": "PHONE-AAAA"}
FAKE_B = {"access_token": "fake-6.1-token-BBBB", "phone_number_id": "PHONE-BBBB"}


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
    for var in ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_API_VERSION",
                "GOOGLE_SERVICE_ACCOUNT_FILE", "GMAIL_DELEGATED_USER", "GOOGLE_CALENDAR_ID"):
        monkeypatch.delenv(var, raising=False)
    yield engine
    engine.dispose()
    ce._fernet_for_version.cache_clear()


def _context(org_id: int) -> TenantContext:
    return TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM, source="test")


def _configure_whatsapp(session, org_id: int, fake: dict) -> None:
    ic.configure_integration(
        session, _context(org_id), IntegrationProvider.WHATSAPP,
        secret_fields={"access_token": fake["access_token"]},
        configuration_fields={"phone_number_id": fake["phone_number_id"]},
    )


class _FakeResponse:
    def __init__(self):
        self.ok = True
        self.status_code = 200
        self.content = b"{}"

    def json(self):
        return {"messages": [{"id": "wamid.FAKE"}]}


@pytest.fixture()
def captured_requests(monkeypatch):
    calls: list[dict] = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "json": json})
        return _FakeResponse()

    monkeypatch.setattr("integrations.whatsapp_service.requests.post", _fake_post)
    return calls


# ---------------------------------------------------------------------------
# EXECUTION — queue item tenant resolution
# ---------------------------------------------------------------------------

def test_queue_item_a_resolves_org_a(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Exec A", slug="exec-a")
        _configure_whatsapp(session, org_a.id, FAKE_A)
        session.commit()
        context_a = _context(org_a.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "919999999901", "body": "hi A"}, tenant_context=context_a)
    mgr.decide_item(item["id"], "Approved")
    result = mgr.execute_item(item["id"])

    assert result["status"] == "sent"
    assert captured_requests[-1]["headers"]["Authorization"] == f"Bearer {FAKE_A['access_token']}"


def test_queue_item_b_resolves_org_b(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org_b = organization_service.create_organization(session, name="Exec B", slug="exec-b")
        _configure_whatsapp(session, org_b.id, FAKE_B)
        session.commit()
        context_b = _context(org_b.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "919999999902", "body": "hi B"}, tenant_context=context_b)
    mgr.decide_item(item["id"], "Approved")
    result = mgr.execute_item(item["id"])

    assert result["status"] == "sent"
    assert captured_requests[-1]["headers"]["Authorization"] == f"Bearer {FAKE_B['access_token']}"


def test_interleaved_a_b_a_execution_no_bleed(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Interleave Exec A", slug="interleave-exec-a")
        org_b = organization_service.create_organization(session, name="Interleave Exec B", slug="interleave-exec-b")
        _configure_whatsapp(session, org_a.id, FAKE_A)
        _configure_whatsapp(session, org_b.id, FAKE_B)
        session.commit()
        context_a, context_b = _context(org_a.id), _context(org_b.id)

    item_a1 = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "a1"}, tenant_context=context_a)
    item_b = mgr.prepare_execution("whatsapp", "send_text", {"to": "2", "body": "b"}, tenant_context=context_b)
    item_a2 = mgr.prepare_execution("whatsapp", "send_text", {"to": "3", "body": "a2"}, tenant_context=context_a)
    for item in (item_a1, item_b, item_a2):
        mgr.decide_item(item["id"], "Approved")

    mgr.execute_item(item_a1["id"])
    assert captured_requests[-1]["headers"]["Authorization"] == f"Bearer {FAKE_A['access_token']}"

    mgr.execute_item(item_b["id"])
    assert captured_requests[-1]["headers"]["Authorization"] == f"Bearer {FAKE_B['access_token']}"

    mgr.execute_item(item_a2["id"])
    assert captured_requests[-1]["headers"]["Authorization"] == f"Bearer {FAKE_A['access_token']}"


def test_exact_foreign_queue_item_id_always_resolves_its_own_organization(isolated, captured_requests) -> None:
    """The adversarial framing of section 4: execute_item() takes no
    caller-supplied organization at all — it is structurally impossible
    to pass "context A" against "item B", since there is no context
    parameter. This proves the only context that is EVER used to
    execute item B is B's own, regardless of which organization was
    "current" most recently."""
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Foreign A", slug="foreign-a")
        org_b = organization_service.create_organization(session, name="Foreign B", slug="foreign-b")
        _configure_whatsapp(session, org_a.id, FAKE_A)
        _configure_whatsapp(session, org_b.id, FAKE_B)
        session.commit()
        context_a, context_b = _context(org_a.id), _context(org_b.id)

    item_a = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "a"}, tenant_context=context_a)
    item_b = mgr.prepare_execution("whatsapp", "send_text", {"to": "2", "body": "b"}, tenant_context=context_b)
    mgr.decide_item(item_a["id"], "Approved")
    mgr.decide_item(item_b["id"], "Approved")

    # Execute A first (establishes "recent" state), then execute B's
    # exact item id — must use B's credentials, never A's.
    mgr.execute_item(item_a["id"])
    mgr.execute_item(item_b["id"])
    assert captured_requests[-1]["headers"]["Authorization"] == f"Bearer {FAKE_B['access_token']}"


def test_missing_organization_id_fails_closed_no_default_fallback(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        transitional_org = organization_service.create_organization(session, name="Transitional", slug=ORG_SLUG)
        _configure_whatsapp(session, transitional_org.id, FAKE_A)  # transitional org IS configured
        session.commit()
        context = _context(transitional_org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)
    mgr.decide_item(item["id"], "Approved")

    # Simulate a malformed/legacy (pre-6.1) item lacking organization_id.
    rows = mgr._load()
    for row in rows:
        if row["id"] == item["id"]:
            row.pop("organization_id", None)
    mgr._save(rows)

    result = mgr.execute_item(item["id"])
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert captured_requests == []  # never sent — no fallback to the transitional org's real credentials


def test_inactive_organization_fails_closed(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Inactive Org", slug="inactive-org")
        _configure_whatsapp(session, org.id, FAKE_A)
        session.commit()
        context = _context(org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)
    mgr.decide_item(item["id"], "Approved")

    with Session(isolated) as session:
        organization_service.deactivate_organization(session, context.organization_id)
        session.commit()

    result = mgr.execute_item(item["id"])
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert captured_requests == []


def test_nonexistent_organization_fails_closed(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Ghost Org", slug="ghost-org")
        _configure_whatsapp(session, org.id, FAKE_A)
        session.commit()
        context = _context(org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)
    mgr.decide_item(item["id"], "Approved")

    rows = mgr._load()
    for row in rows:
        if row["id"] == item["id"]:
            row["organization_id"] = 987654321  # does not exist
    mgr._save(rows)

    result = mgr.execute_item(item["id"])
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert captured_requests == []


def test_malformed_organization_id_type_fails_closed(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Malformed Org", slug="malformed-org")
        _configure_whatsapp(session, org.id, FAKE_A)
        session.commit()
        context = _context(org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)
    mgr.decide_item(item["id"], "Approved")

    rows = mgr._load()
    for row in rows:
        if row["id"] == item["id"]:
            row["organization_id"] = "not-an-int"
    mgr._save(rows)

    result = mgr.execute_item(item["id"])
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert captured_requests == []


# ---------------------------------------------------------------------------
# PREPARE-TIME — resolution failure never creates an org-less item
# ---------------------------------------------------------------------------

def test_prepare_execution_raises_when_no_organization_resolvable(isolated, monkeypatch) -> None:
    def _broken(*a, **kw):
        raise RuntimeError("simulated resolution failure")

    monkeypatch.setattr(mgr, "_current_tenant_context", _broken)
    with pytest.raises(Exception):
        mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"})


# ---------------------------------------------------------------------------
# APPROVAL — organization mismatch between approval and item is rejected
# ---------------------------------------------------------------------------

def test_approval_organization_mismatch_blocks_execution(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Mismatch A", slug="mismatch-a")
        org_b = organization_service.create_organization(session, name="Mismatch B", slug="mismatch-b")
        _configure_whatsapp(session, org_a.id, FAKE_A)
        _configure_whatsapp(session, org_b.id, FAKE_B)
        session.commit()
        context_a, org_b_id = _context(org_a.id), org_b.id

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context_a)
    mgr.decide_item(item["id"], "Approved")

    # Tamper with the legacy approval's stamped organization_id so it no
    # longer matches its own queue item.
    from core.memory import load_memory, update_memory

    def _tamper(memory):
        for row in memory.get("approvals", []):
            if row.get("id") == item["approval_id"]:
                row["data"]["organization_id"] = org_b_id

    update_memory(_tamper)

    result = mgr.execute_item(item["id"])
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert "does not match" in result["detail"]
    assert captured_requests == []


def test_approval_and_item_stamped_with_same_organization_by_default(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Consistency Org", slug="consistency-org")
        session.commit()
        context = _context(org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)
    approval_row = mgr._get_approval_row(item["approval_id"])
    assert item["organization_id"] == org.id
    assert approval_row["data"]["organization_id"] == org.id


# ---------------------------------------------------------------------------
# SHADOW-SYNC — items/approvals land under their OWN organization, not
# always the transitional default
# ---------------------------------------------------------------------------

def test_shadow_sync_uses_items_own_organization_not_transitional(isolated) -> None:
    with Session(isolated) as session:
        transitional = organization_service.create_organization(session, name="Transitional Org", slug=ORG_SLUG)
        other = organization_service.create_organization(session, name="Other Org", slug="other-org-shadow")
        session.commit()
        transitional_id, other_id = transitional.id, other.id

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=_context(other_id))

    with Session(isolated) as session:
        rows = session.query(ExecutionQueueItem).filter(ExecutionQueueItem.external_id == item["id"]).all()
    assert len(rows) == 1
    assert rows[0].organization_id == other_id
    assert rows[0].organization_id != transitional_id


# ---------------------------------------------------------------------------
# STATUS fix — configure_integration() semantics
# ---------------------------------------------------------------------------

def test_fresh_config_only_integration_stays_unconfigured(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Fresh Config Org", slug="fresh-config-org")
        row = ic.configure_integration(session, _context(org.id), IntegrationProvider.WHATSAPP, configuration_fields={"phone_number_id": "P-1"})
        session.commit()
        assert row.status == IntegrationStatus.UNCONFIGURED


def test_fresh_integration_with_secret_becomes_active(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Fresh Secret Org", slug="fresh-secret-org")
        row = ic.configure_integration(session, _context(org.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        session.commit()
        assert row.status == IntegrationStatus.ACTIVE


def test_existing_active_integration_remains_active_on_config_only_update(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Stays Active Org", slug="stays-active-org")
        context = _context(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        session.commit()
        row = ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, configuration_fields={"phone_number_id": "NEW-PHONE-ID"})
        session.commit()
        assert row.status == IntegrationStatus.ACTIVE
        assert row.encrypted_credentials is not None  # secret untouched


def test_disabled_integration_stays_disabled_on_config_only_update(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Stays Disabled Org", slug="stays-disabled-org")
        context = _context(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        session.commit()
        ic.disable_integration(session, context, IntegrationProvider.WHATSAPP)
        session.commit()
        row = ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, configuration_fields={"phone_number_id": "NEW-PHONE-ID"})
        session.commit()
        assert row.status == IntegrationStatus.DISABLED  # config-only update never silently reactivates


def test_disabled_integration_reactivates_when_secret_is_reconfigured(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Reconfigure Org", slug="reconfigure-org")
        context = _context(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        session.commit()
        ic.disable_integration(session, context, IntegrationProvider.WHATSAPP)
        session.commit()
        row = ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_B)
        session.commit()
        assert row.status == IntegrationStatus.ACTIVE  # explicit reconfigure = reactivate


def test_corrupted_secret_behavior_unchanged_by_status_fix(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Corrupt Unchanged Org", slug="corrupt-unchanged-org")
        context = _context(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        session.commit()
        row = ic.get_integration(session, org.id, IntegrationProvider.WHATSAPP)
        row.encrypted_credentials = row.encrypted_credentials[:-6] + "ZZZZZZ"
        session.commit()

        result = ic.resolve_credentials(session, context, IntegrationProvider.WHATSAPP)
        session.commit()
        assert result is None
        row = ic.get_integration(session, org.id, IntegrationProvider.WHATSAPP)
        assert row.status == IntegrationStatus.ERROR
        assert row.last_error_code == "decryption_failed"


# ---------------------------------------------------------------------------
# REDACTION / secret safety through the actual execution path
# ---------------------------------------------------------------------------

def test_fake_secret_absent_from_execution_audit_and_reports(isolated, captured_requests) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Secret Safety Org", slug="secret-safety-org")
        _configure_whatsapp(session, org.id, {"access_token": "fake-MUST-NOT-LEAK-token", "phone_number_id": "PHONE-SAFE"})
        session.commit()
        context = _context(org.id)

    item = mgr.prepare_execution("whatsapp", "send_text", {"to": "1", "body": "x"}, tenant_context=context)
    mgr.decide_item(item["id"], "Approved")
    result = mgr.execute_item(item["id"])

    assert "fake-MUST-NOT-LEAK-token" not in str(result)

    from core.memory import load_memory
    memory = load_memory()
    assert "fake-MUST-NOT-LEAK-token" not in str(memory.get("security_audit_log", []))
    assert "fake-MUST-NOT-LEAK-token" not in str(memory.get("reports", []))

    with Session(isolated) as session:
        from core.db.models.operations import SecurityAuditEvent
        events = session.query(SecurityAuditEvent).all()
        for event in events:
            assert "fake-MUST-NOT-LEAK-token" not in (event.detail or "")


def test_redact_still_available_and_correct_though_unwired() -> None:
    """Confirms the Phase 6.1 decision (Option B — document, don't force
    a wiring) didn't remove or break the utility itself."""
    redacted = ce.redact({"access_token": "fake-unwired-secret", "phone_number_id": "PHONE-1"})
    assert redacted["access_token"] == "***REDACTED***"
    assert redacted["phone_number_id"] == "PHONE-1"
