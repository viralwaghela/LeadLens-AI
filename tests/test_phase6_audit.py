"""Independent adversarial audit tests for V2 Phase 6 (per-organization
integration credentials). Written separately from
tests/test_phase6_integration_credentials.py as a second, independently-
designed adversarial pass — same isolation discipline (private in-memory
DB, private temp legacy store, obviously-fake test secrets, never a real
DATABASE_URL, never a real provider API call). import _bootstrap first,
same as every other file in tests/.
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
import services.appointment_messaging as appt_msg
import scripts.migrate_integration_credentials as migrate_mod
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.integration import IntegrationProvider, IntegrationStatus
from core.db.session import make_engine
from core.identity import organization_service
from core.identity.tenant_context import ActorType, TenantContext

ORG_SLUG = "phase6-audit-clinic"

FAKE_A = {"access_token": "fake-audit-token-AAAA", "phone_number_id": "PHONE-AAAA"}
FAKE_B = {"access_token": "fake-audit-token-BBBB", "phone_number_id": "PHONE-BBBB"}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", ORG_SLUG)
    monkeypatch.setattr(migrate_mod, "DEFAULT_ORGANIZATION_SLUG", ORG_SLUG)
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
    return TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM, source="audit")


# ---------------------------------------------------------------------------
# 1. configure_integration() with configuration-only (no secret) on a
#    brand-new row — does it ever produce a resolvable "ACTIVE" state
#    with no real secret? Must fail closed, never resolve garbage.
# ---------------------------------------------------------------------------

def test_configure_without_secret_never_resolves_a_usable_credential(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Config-only Org", slug="config-only-org")
        org_id = org.id
        context = _context(org_id)
        row = ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, configuration_fields={"phone_number_id": "PHONE-ONLY"})
        session.commit()
        assert row.encrypted_credentials is None  # no secret was ever supplied

        # Whatever status this leaves the row in, resolution must never
        # hand back a "successful" credential when no secret exists.
        result = ic.resolve_credentials(session, context, IntegrationProvider.WHATSAPP)
        session.commit()
    assert result is None


def test_configure_without_secret_status_stays_unconfigured(isolated) -> None:
    """Phase 6.1 fix (was a Phase 6 audit finding): configure_integration()
    no longer sets status=ACTIVE unconditionally. A fresh row with only
    configuration_fields and no secret ever supplied must stay
    UNCONFIGURED, not misleadingly ACTIVE."""
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Status Wart Org", slug="status-wart-org")
        context = _context(org.id)
        row = ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, configuration_fields={"phone_number_id": "PHONE-ONLY"})
        session.commit()
        assert row.status == IntegrationStatus.UNCONFIGURED
        assert row.encrypted_credentials is None


# ---------------------------------------------------------------------------
# 2. Interleaved A/B calls across all three providers — no shared-state
#    leakage through the encryption key cache or anywhere else.
# ---------------------------------------------------------------------------

def test_interleaved_whatsapp_resolution_across_orgs_never_bleeds(isolated) -> None:
    from services.integration_clients import get_whatsapp_client

    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Interleave A", slug="interleave-a")
        org_b = organization_service.create_organization(session, name="Interleave B", slug="interleave-b")
        ic.configure_integration(session, _context(org_a.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        ic.configure_integration(session, _context(org_b.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_B, configuration_fields={"phone_number_id": FAKE_B["phone_number_id"]})
        session.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    # Interleave: A, B, A, B, B, A — no ordering-dependent bleed.
    sequence = [org_a_id, org_b_id, org_a_id, org_b_id, org_b_id, org_a_id]
    expected = {org_a_id: FAKE_A["access_token"], org_b_id: FAKE_B["access_token"]}
    for org_id in sequence:
        client = get_whatsapp_client(_context(org_id))
        assert client.token == expected[org_id]


# ---------------------------------------------------------------------------
# 3. Master-key failure with a real encrypted credential already stored —
#    resolving org A must never fall back to org B, or to env, or to any
#    other organization's row.
# ---------------------------------------------------------------------------

def test_master_key_failure_does_not_leak_to_other_organization(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Key Fail A", slug="keyfail-a")
        org_b = organization_service.create_organization(session, name="Key Fail B", slug="keyfail-b")
        ic.configure_integration(session, _context(org_a.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        ic.configure_integration(session, _context(org_b.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_B, configuration_fields={"phone_number_id": FAKE_B["phone_number_id"]})
        session.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()
    with Session(isolated) as session:
        result_a = ic.resolve_credentials(session, _context(org_a_id), IntegrationProvider.WHATSAPP)
        session.commit()
    with Session(isolated) as session:
        result_b = ic.resolve_credentials(session, _context(org_b_id), IntegrationProvider.WHATSAPP)
        session.commit()
    assert result_a is None
    assert result_b is None  # both fail closed independently — neither inherits the other's data
    ce._fernet_for_version.cache_clear()


# ---------------------------------------------------------------------------
# 4. DB unavailable in a "multi-org" scenario — resolution for any
#    organization must degrade to None/dry-run, never silently reuse a
#    deployment-global credential for an arbitrary organization.
# ---------------------------------------------------------------------------

def test_db_unavailable_never_substitutes_a_deployment_global_credential(isolated, monkeypatch) -> None:
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", True)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-deployment-global-should-not-leak")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-GLOBAL")

    def _broken_get_engine():
        raise RuntimeError("simulated credential DB outage")

    monkeypatch.setattr(ic, "_get_engine", _broken_get_engine)
    arbitrary_org_context = TenantContext(organization_id=987654, actor_type=ActorType.SYSTEM)
    result = ic.resolve_provider_credentials(arbitrary_org_context, IntegrationProvider.WHATSAPP)
    assert result is None  # never substitutes the env-global credential for an arbitrary org during an outage


# ---------------------------------------------------------------------------
# 5. Admin operations cannot touch a foreign organization's row.
# ---------------------------------------------------------------------------

def test_disable_integration_only_affects_own_organization(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Disable A", slug="disable-a")
        org_b = organization_service.create_organization(session, name="Disable B", slug="disable-b")
        ic.configure_integration(session, _context(org_a.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        ic.configure_integration(session, _context(org_b.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_B, configuration_fields={"phone_number_id": FAKE_B["phone_number_id"]})
        session.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

        # Disabling under A's context must never touch B's row.
        ic.disable_integration(session, _context(org_a_id), IntegrationProvider.WHATSAPP)
        session.commit()

        row_a = ic.get_integration(session, org_a_id, IntegrationProvider.WHATSAPP)
        row_b = ic.get_integration(session, org_b_id, IntegrationProvider.WHATSAPP)
        assert row_a.status == IntegrationStatus.DISABLED
        assert row_b.status == IntegrationStatus.ACTIVE  # untouched


def test_safe_metadata_never_includes_the_secret(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Metadata Org", slug="metadata-org")
        row = ic.configure_integration(session, _context(org.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        session.commit()
        metadata = ic.safe_metadata(row)
    serialized = str(metadata)
    assert FAKE_A["access_token"] not in serialized
    assert "encrypted_credentials" not in metadata


# ---------------------------------------------------------------------------
# 6. Transactional auto-action path (appointment_messaging) — org B must
#    never see org A's WhatsApp credential just because A was resolved
#    first in the same process.
# ---------------------------------------------------------------------------

def test_transactional_path_does_not_cache_the_first_resolved_organization(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Transactional A", slug=ORG_SLUG)
        ic.configure_integration(session, _context(org_a.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_A, configuration_fields={"phone_number_id": FAKE_A["phone_number_id"]})
        session.commit()

    first_client = appt_msg._whatsapp_client()
    assert first_client.token == FAKE_A["access_token"]

    # Now the transitional org's row is disabled — a second resolution
    # in the same process must reflect that immediately, not reuse a
    # cached first-resolved client/credential.
    with Session(isolated) as session:
        org_a = organization_service.get_organization_by_slug(session, ORG_SLUG)
        ic.disable_integration(session, _context(org_a.id), IntegrationProvider.WHATSAPP)
        session.commit()

    second_client = appt_msg._whatsapp_client()
    assert second_client.token == ""  # disabled -> no credential -> falls back to adapter's own env read (empty) -> dry-run
    assert second_client.dry_run is True


# ---------------------------------------------------------------------------
# 7. Migration script never auto-selects "the first organization" — it
#    always requires an explicit (even if defaulted) organization.
# ---------------------------------------------------------------------------

def test_migrate_script_organization_selection_is_always_explicit(isolated) -> None:
    import inspect
    import scripts.migrate_integration_credentials as migrate_mod

    source = inspect.getsource(migrate_mod._resolve_organization)
    assert ".first()" not in source
    assert "LIMIT 1" not in source.upper()
