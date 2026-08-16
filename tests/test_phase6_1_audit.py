"""Independent adversarial audit tests for V2 Phase 6.1 (multi-organization
execution-queue hardening). Written separately from
tests/test_phase6_1_execution_hardening.py as a second, independently-
designed adversarial pass. import _bootstrap first, same as every other
file in tests/.
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

ORG_SLUG = "phase6-1-audit-clinic"

FAKE_A = {"access_token": "fake-audit61-token-AAAA", "phone_number_id": "PHONE-AAAA"}
FAKE_B = {"access_token": "fake-audit61-token-BBBB", "phone_number_id": "PHONE-BBBB"}


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
    return TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM, source="audit")


def _configure_whatsapp(session, org_id: int, fake: dict) -> None:
    ic.configure_integration(
        session, _context(org_id), IntegrationProvider.WHATSAPP,
        secret_fields={"access_token": fake["access_token"]},
        configuration_fields={"phone_number_id": fake["phone_number_id"]},
    )


# ---------------------------------------------------------------------------
# Candidate finding: is prepare_execution()'s fingerprint dedup
# organization-scoped, or does it scan the GLOBAL queue regardless of org?
# ---------------------------------------------------------------------------

def test_identical_payload_from_two_organizations_does_not_cross_contaminate(isolated) -> None:
    """Org A and Org B both prepare an action with the exact same
    provider/action/payload (a real scenario: two clinics sending an
    identically-worded templated reminder to a patient who happens to
    share a phone number, or any coincidental payload collision).
    Org B's action must be its OWN queue item, stamped with Org B's
    organization_id — never silently resolve to Org A's existing item."""
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Fingerprint A", slug="fingerprint-a")
        org_b = organization_service.create_organization(session, name="Fingerprint B", slug="fingerprint-b")
        session.commit()
        context_a, context_b = _context(org_a.id), _context(org_b.id)

    payload = {"to": "919999999999", "body": "Hi, this is your appointment reminder."}
    item_a = mgr.prepare_execution("whatsapp", "send_text", payload, tenant_context=context_a)
    item_b = mgr.prepare_execution("whatsapp", "send_text", payload, tenant_context=context_b)

    assert item_a["id"] != item_b["id"], (
        "Org B's prepare_execution() call returned Org A's existing queue item "
        "instead of creating its own — the fingerprint-based dedup check in "
        "prepare_execution() is not organization-scoped."
    )
    assert item_a["organization_id"] == org_a.id
    assert item_b["organization_id"] == org_b.id
