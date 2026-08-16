"""V2 Phase 6 tests: per-organization integration credentials
(encryption, the OrganizationIntegration model, credential resolution,
the transitional environment fallback, and the WhatsApp/Gmail/Calendar
adapter factories).

Every test uses its own private, temporary SQLite database (V2 side)
and a private temp DATABASE_FOLDER (legacy side) — never the tracked
local dev database, never a real DATABASE_URL, never a real provider
API. All secrets used here are obviously-fake test values. import
_bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import core.memory as business_memory
import services.credential_encryption as ce
import services.integration_credentials as ic
import services.integration_manager_v21 as mgr
import services.appointment_messaging as appt_msg
import scheduler.run_scheduled_checks as sched
import scripts.migrate_integration_credentials as migrate_mod
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.integration import IntegrationProvider, IntegrationStatus, OrganizationIntegration
from core.db.session import make_engine
from core.identity import organization_service
from core.identity.tenant_context import ActorType, TenantContext, build_system_context, build_transitional_context

ORG_SLUG = "phase6-test-clinic"

FAKE_WHATSAPP_A = {"access_token": "fake-token-ORG-A-11111", "phone_number_id": "PHONE-A-111"}
FAKE_WHATSAPP_B = {"access_token": "fake-token-ORG-B-22222", "phone_number_id": "PHONE-B-222"}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr("core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", ORG_SLUG)
    # scripts/migrate_integration_credentials.py imported DEFAULT_ORGANIZATION_SLUG
    # via `from X import Y`, which freezes the value at first import — the
    # patch above only affects the defining module's attribute, not this
    # already-bound name, so it's patched explicitly too (mirrors Phase 2's
    # `patch.object(migrate_mod, "DEFAULT_STORE", store)` precedent).
    monkeypatch.setattr(migrate_mod, "DEFAULT_ORGANIZATION_SLUG", ORG_SLUG)

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
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


@pytest.fixture()
def encryption_key(monkeypatch):
    """Lightweight fixture for encryption-only tests that don't need a
    database — just a valid master key in the environment."""
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ce._fernet_for_version.cache_clear()
    yield
    ce._fernet_for_version.cache_clear()


def _make_org(session, slug: str, name: str | None = None):
    return organization_service.create_organization(session, name=name or slug, slug=slug)


def _context_for(org_id: int) -> TenantContext:
    return TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM, source="test")


# ---------------------------------------------------------------------------
# ENCRYPTION
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip(encryption_key) -> None:
    ciphertext, version = ce.encrypt_credential_fields({"access_token": "fake-secret-abc123"})
    assert "fake-secret-abc123" not in ciphertext
    data = ce.decrypt_credential_fields(ciphertext, version)
    assert data == {"access_token": "fake-secret-abc123"}


def test_encrypted_representation_never_contains_plaintext(encryption_key) -> None:
    ciphertext, _ = ce.encrypt_credential_fields({"access_token": "super-fake-token-999", "other": "value"})
    assert "super-fake-token-999" not in ciphertext
    assert "value" not in ciphertext


def test_decrypt_missing_key_raises_key_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()
    with pytest.raises(ce.EncryptionKeyUnavailable):
        ce.encrypt_credential_fields({"access_token": "x"})
    ce._fernet_for_version.cache_clear()


def test_decrypt_with_wrong_key_raises_decryption_error(encryption_key, monkeypatch) -> None:
    ciphertext, version = ce.encrypt_credential_fields({"access_token": "fake-secret"})
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ce._fernet_for_version.cache_clear()
    with pytest.raises(ce.CredentialDecryptionError):
        ce.decrypt_credential_fields(ciphertext, version)
    ce._fernet_for_version.cache_clear()


def test_decrypt_corrupted_ciphertext_raises_decryption_error(encryption_key) -> None:
    ciphertext, version = ce.encrypt_credential_fields({"access_token": "fake-secret"})
    corrupted = ciphertext[:-4] + "abcd"
    with pytest.raises(ce.CredentialDecryptionError):
        ce.decrypt_credential_fields(corrupted, version)


def test_redact_hides_known_secret_field_names() -> None:
    redacted = ce.redact({"access_token": "fake-secret-value", "phone_number_id": "PHONE-123"})
    assert redacted["access_token"] == "***REDACTED***"
    assert redacted["phone_number_id"] == "PHONE-123"  # not a secret field name — preserved


# ---------------------------------------------------------------------------
# MODEL
# ---------------------------------------------------------------------------

def test_org_provider_uniqueness_enforced(isolated) -> None:
    with Session(isolated) as session:
        org = _make_org(session, "uniq-org")
        session.add(OrganizationIntegration(organization_id=org.id, provider=IntegrationProvider.WHATSAPP, status=IntegrationStatus.UNCONFIGURED))
        session.commit()
        session.add(OrganizationIntegration(organization_id=org.id, provider=IntegrationProvider.WHATSAPP, status=IntegrationStatus.UNCONFIGURED))
        with pytest.raises(IntegrityError):
            session.commit()


def test_a_and_b_rows_are_independent(isolated) -> None:
    with Session(isolated) as session:
        org_a = _make_org(session, "model-org-a")
        org_b = _make_org(session, "model-org-b")
        context_a = _context_for(org_a.id)
        context_b = _context_for(org_b.id)
        ic.configure_integration(session, context_a, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        ic.configure_integration(session, context_b, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_B, configuration_fields={"phone_number_id": "PHONE-B-222"})
        session.commit()

        row_a = ic.get_integration(session, org_a.id, IntegrationProvider.WHATSAPP)
        row_b = ic.get_integration(session, org_b.id, IntegrationProvider.WHATSAPP)
        assert row_a.encrypted_credentials != row_b.encrypted_credentials
        secret_a = ce.decrypt_credential_fields(row_a.encrypted_credentials, row_a.encryption_key_version)
        secret_b = ce.decrypt_credential_fields(row_b.encrypted_credentials, row_b.encryption_key_version)
        assert secret_a["access_token"] == FAKE_WHATSAPP_A["access_token"]
        assert secret_b["access_token"] == FAKE_WHATSAPP_B["access_token"]


def test_disabled_status_persists(isolated) -> None:
    with Session(isolated) as session:
        org = _make_org(session, "disable-org")
        context = _context_for(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()
        ic.disable_integration(session, context, IntegrationProvider.WHATSAPP)
        session.commit()
        row = ic.get_integration(session, org.id, IntegrationProvider.WHATSAPP)
        assert row.status == IntegrationStatus.DISABLED


# ---------------------------------------------------------------------------
# RESOLUTION
# ---------------------------------------------------------------------------

def test_resolve_a_gets_a_b_gets_b(isolated) -> None:
    with Session(isolated) as session:
        org_a = _make_org(session, "resolve-org-a")
        org_b = _make_org(session, "resolve-org-b")
        ic.configure_integration(session, _context_for(org_a.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        ic.configure_integration(session, _context_for(org_b.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_B, configuration_fields={"phone_number_id": "PHONE-B-222"})
        session.commit()

        resolved_a = ic.resolve_credentials(session, _context_for(org_a.id), IntegrationProvider.WHATSAPP)
        resolved_b = ic.resolve_credentials(session, _context_for(org_b.id), IntegrationProvider.WHATSAPP)
    assert resolved_a.secret["access_token"] == FAKE_WHATSAPP_A["access_token"]
    assert resolved_b.secret["access_token"] == FAKE_WHATSAPP_B["access_token"]
    assert resolved_a.secret["access_token"] != resolved_b.secret["access_token"]


def test_no_function_can_fetch_a_foreign_integration_by_raw_id(isolated) -> None:
    """Adversarial: an attacker who knows Org B's exact row id has no
    function in this module that accepts a raw integration id at all —
    every read is scoped by (organization_id, provider)."""
    import inspect

    for name, fn in vars(ic).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        if fn.__module__ != ic.__name__:
            continue
        sig = inspect.signature(fn)
        assert "integration_id" not in sig.parameters
        assert "id" not in sig.parameters


def test_resolve_missing_provider_returns_none(isolated) -> None:
    with Session(isolated) as session:
        org = _make_org(session, "missing-provider-org")
        resolved = ic.resolve_credentials(session, _context_for(org.id), IntegrationProvider.GMAIL)
    assert resolved is None


def test_resolve_disabled_integration_returns_none_no_fallback(isolated, monkeypatch) -> None:
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", True)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-env-fallback-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-ENV")
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)  # the transitional org itself
        context = _context_for(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()
        ic.disable_integration(session, context, IntegrationProvider.WHATSAPP)
        session.commit()
        resolved = ic.resolve_credentials(session, context, IntegrationProvider.WHATSAPP)
    assert resolved is None  # disabled -> never falls back, even for the transitional org


# ---------------------------------------------------------------------------
# ENV FALLBACK — spec sections 7-8
# ---------------------------------------------------------------------------

def test_env_fallback_allowed_for_transitional_org(isolated, monkeypatch) -> None:
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", True)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-env-fallback-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-ENV")
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)  # exactly the transitional/default org
        resolved = ic.resolve_credentials(session, _context_for(org.id), IntegrationProvider.WHATSAPP)
    assert resolved is not None
    assert resolved.source == "env_fallback"
    assert resolved.secret["access_token"] == "fake-env-fallback-token"


def test_env_fallback_denied_for_unrelated_org(isolated, monkeypatch) -> None:
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", True)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-env-fallback-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-ENV")
    with Session(isolated) as session:
        unrelated_org = _make_org(session, "some-other-real-clinic")
        resolved = ic.resolve_credentials(session, _context_for(unrelated_org.id), IntegrationProvider.WHATSAPP)
    assert resolved is None  # section 8's hard rule — never inherits env credentials


def test_env_fallback_disabled_by_flag(isolated, monkeypatch) -> None:
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", False)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-env-fallback-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-ENV")
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)
        resolved = ic.resolve_credentials(session, _context_for(org.id), IntegrationProvider.WHATSAPP)
    assert resolved is None


def test_tenant_credential_overrides_env_fallback(isolated, monkeypatch) -> None:
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", True)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-env-fallback-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-ENV")
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)
        context = _context_for(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()
        resolved = ic.resolve_credentials(session, context, IntegrationProvider.WHATSAPP)
    assert resolved.source == "tenant"
    assert resolved.secret["access_token"] == FAKE_WHATSAPP_A["access_token"]


# ---------------------------------------------------------------------------
# WHATSAPP / GMAIL / CALENDAR adapter factories
# ---------------------------------------------------------------------------

def test_whatsapp_factory_a_uses_a_b_uses_b(isolated) -> None:
    from services.integration_clients import get_whatsapp_client

    with Session(isolated) as session:
        org_a = _make_org(session, "wa-factory-a")
        org_b = _make_org(session, "wa-factory-b")
        ic.configure_integration(session, _context_for(org_a.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        ic.configure_integration(session, _context_for(org_b.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_B, configuration_fields={"phone_number_id": "PHONE-B-222"})
        session.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    client_a = get_whatsapp_client(_context_for(org_a_id))
    client_b = get_whatsapp_client(_context_for(org_b_id))
    assert client_a.token == FAKE_WHATSAPP_A["access_token"]
    assert client_a.phone_number_id == "PHONE-A-111"
    assert client_b.token == FAKE_WHATSAPP_B["access_token"]
    assert client_b.phone_number_id == "PHONE-B-222"
    assert client_a.token != client_b.token


def test_gmail_factory_a_uses_a_b_uses_b(isolated) -> None:
    from services.integration_clients import get_gmail_client

    with Session(isolated) as session:
        org_a = _make_org(session, "gmail-factory-a")
        org_b = _make_org(session, "gmail-factory-b")
        ic.configure_integration(
            session, _context_for(org_a.id), IntegrationProvider.GMAIL,
            secret_fields={"service_account_json": json.dumps({"type": "service_account", "fake": "a"})},
            configuration_fields={"delegated_user": "owner-a@clinic-a.example"},
        )
        ic.configure_integration(
            session, _context_for(org_b.id), IntegrationProvider.GMAIL,
            secret_fields={"service_account_json": json.dumps({"type": "service_account", "fake": "b"})},
            configuration_fields={"delegated_user": "owner-b@clinic-b.example"},
        )
        session.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    client_a = get_gmail_client(_context_for(org_a_id))
    client_b = get_gmail_client(_context_for(org_b_id))
    assert client_a.delegated_user == "owner-a@clinic-a.example"
    assert client_b.delegated_user == "owner-b@clinic-b.example"
    assert client_a.delegated_user != client_b.delegated_user
    assert "\"fake\": \"a\"" in client_a.credentials_json
    assert "\"fake\": \"b\"" in client_b.credentials_json


def test_calendar_factory_a_uses_a_b_uses_b(isolated) -> None:
    from services.integration_clients import get_calendar_client

    with Session(isolated) as session:
        org_a = _make_org(session, "cal-factory-a")
        org_b = _make_org(session, "cal-factory-b")
        ic.configure_integration(
            session, _context_for(org_a.id), IntegrationProvider.GOOGLE_CALENDAR,
            secret_fields={"service_account_json": json.dumps({"type": "service_account", "fake": "cal-a"})},
            configuration_fields={"calendar_id": "calendar-a@group.calendar.google.com"},
        )
        ic.configure_integration(
            session, _context_for(org_b.id), IntegrationProvider.GOOGLE_CALENDAR,
            secret_fields={"service_account_json": json.dumps({"type": "service_account", "fake": "cal-b"})},
            configuration_fields={"calendar_id": "calendar-b@group.calendar.google.com"},
        )
        session.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    client_a = get_calendar_client(_context_for(org_a_id))
    client_b = get_calendar_client(_context_for(org_b_id))
    assert client_a.calendar_id == "calendar-a@group.calendar.google.com"
    assert client_b.calendar_id == "calendar-b@group.calendar.google.com"


def test_missing_credentials_falls_back_to_dry_run_not_a_crash(isolated) -> None:
    """No resolvable credentials at all (no tenant row, fallback off) —
    the adapter must degrade to its own existing dry-run behavior, not
    raise. This is the "do not break existing deployments" guarantee."""
    from services.integration_clients import get_whatsapp_client

    with Session(isolated) as session:
        org = _make_org(session, "unconfigured-org")
        org_id = org.id
    client = get_whatsapp_client(_context_for(org_id))
    assert client.dry_run is True
    result = client.send_text({"to": "919999999999", "body": "test"})
    assert result.success is True
    assert result.status == "simulated"


# ---------------------------------------------------------------------------
# SCHEDULER / APPROVAL-QUEUE propagation
# ---------------------------------------------------------------------------

def test_scheduler_appointment_messaging_resolves_transitional_org_credentials(isolated, monkeypatch) -> None:
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)
        ic.configure_integration(session, _context_for(org.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()

    client = appt_msg._whatsapp_client()
    assert client.token == FAKE_WHATSAPP_A["access_token"]
    assert client.dry_run is False


def test_approval_queue_execute_item_resolves_correct_org_credentials(isolated) -> None:
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)
        ic.configure_integration(session, _context_for(org.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()

    client = mgr._whatsapp_client()
    assert client.token == FAKE_WHATSAPP_A["access_token"]
    assert client.phone_number_id == "PHONE-A-111"


def test_integration_manager_status_reflects_tenant_credential(isolated) -> None:
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)
        ic.configure_integration(session, _context_for(org.id), IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()

    statuses = mgr.integration_statuses()
    whatsapp_status = next(s for s in statuses if s["provider"] == "WhatsApp Business")
    assert whatsapp_status["configured"] is True
    assert whatsapp_status["mode"] == "live"


# ---------------------------------------------------------------------------
# AUDIT — no secrets
# ---------------------------------------------------------------------------

def test_configure_integration_audit_never_contains_secret(isolated, monkeypatch, capfd) -> None:
    import services.tenant_operational_sync as tos
    monkeypatch.setattr(tos, "_ENGINE", isolated)
    monkeypatch.setattr(tos, "TENANT_CONTEXT_ENABLED", True)

    with Session(isolated) as session:
        org = _make_org(session, "audit-org")
        ic.configure_integration(session, _context_for(org.id), IntegrationProvider.WHATSAPP, secret_fields={"access_token": "fake-super-secret-audit-check"}, configuration_fields={"phone_number_id": "PHONE-X"})
        session.commit()

    from core.db.models.operations import SecurityAuditEvent
    with Session(isolated) as session:
        rows = session.query(SecurityAuditEvent).all()
    for row in rows:
        assert "fake-super-secret-audit-check" not in (row.detail or "")
        assert "fake-super-secret-audit-check" not in (row.action or "")


# ---------------------------------------------------------------------------
# MIGRATION SCRIPT
# ---------------------------------------------------------------------------

def test_migration_script_dry_run_writes_nothing(isolated, monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-migrate-token-dryrun")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-MIGRATE")
    import scripts.migrate_integration_credentials as migrate_mod

    messages = migrate_mod.migrate(
        organization_slug=ORG_SLUG, providers=[IntegrationProvider.WHATSAPP],
        dry_run=True, force=False, engine=isolated,
    )
    assert any("DRY RUN" in m for m in messages)
    with Session(isolated) as session:
        org = organization_service.get_organization_by_slug(session, ORG_SLUG)
        assert org is None or ic.get_integration(session, org.id, IntegrationProvider.WHATSAPP) is None


def test_migration_script_is_idempotent_and_never_overwrites(isolated, monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-migrate-token-real")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-MIGRATE-REAL")
    import scripts.migrate_integration_credentials as migrate_mod

    first = migrate_mod.migrate(organization_slug=ORG_SLUG, providers=[IntegrationProvider.WHATSAPP], dry_run=False, force=False, engine=isolated)
    assert any("migrated" in m for m in first)

    with Session(isolated) as session:
        org = organization_service.get_organization_by_slug(session, ORG_SLUG)
        row = ic.get_integration(session, org.id, IntegrationProvider.WHATSAPP)
        first_ciphertext = row.encrypted_credentials

    # Rerun with a DIFFERENT env token — must not overwrite without --force.
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-migrate-token-DIFFERENT")
    second = migrate_mod.migrate(organization_slug=ORG_SLUG, providers=[IntegrationProvider.WHATSAPP], dry_run=False, force=False, engine=isolated)
    assert any("not overwritten" in m for m in second)

    with Session(isolated) as session:
        org = organization_service.get_organization_by_slug(session, ORG_SLUG)
        row = ic.get_integration(session, org.id, IntegrationProvider.WHATSAPP)
        assert row.encrypted_credentials == first_ciphertext  # unchanged


def test_migration_script_never_prints_secret_values(isolated, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-token-MUST-NOT-APPEAR-IN-OUTPUT")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-MIGRATE")
    import scripts.migrate_integration_credentials as migrate_mod

    messages = migrate_mod.migrate(organization_slug=ORG_SLUG, providers=[IntegrationProvider.WHATSAPP], dry_run=False, force=False, engine=isolated)
    joined = " ".join(messages)
    assert "fake-token-MUST-NOT-APPEAR-IN-OUTPUT" not in joined


# ---------------------------------------------------------------------------
# FAILURE-CLOSED
# ---------------------------------------------------------------------------

def test_resolve_provider_credentials_db_unavailable_returns_none(isolated, monkeypatch) -> None:
    def _broken_make_engine(*a, **kw):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(ic, "_get_engine", _broken_make_engine)
    context = TenantContext(organization_id=1, actor_type=ActorType.SYSTEM)
    result = ic.resolve_provider_credentials(context, IntegrationProvider.WHATSAPP)
    assert result is None


def test_encryption_key_unavailable_marks_error_and_returns_none_no_fallback(isolated, monkeypatch) -> None:
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", True)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "fake-env-should-never-be-used")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE-ENV")
    with Session(isolated) as session:
        org = _make_org(session, ORG_SLUG)
        org_id = org.id
        context = _context_for(org_id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()

    # Master key vanishes (e.g. rotated away / misconfigured deploy).
    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()
    with Session(isolated) as session:
        result = ic.resolve_credentials(session, _context_for(org_id), IntegrationProvider.WHATSAPP)
        session.commit()
    assert result is None  # never falls back to env, even though fallback is enabled

    with Session(isolated) as session:
        row = ic.get_integration(session, org_id, IntegrationProvider.WHATSAPP)
        assert row.status == IntegrationStatus.ERROR
        assert row.last_error_code == "key_unavailable"
    ce._fernet_for_version.cache_clear()


def test_corrupted_ciphertext_marks_error_and_returns_none(isolated) -> None:
    with Session(isolated) as session:
        org = _make_org(session, "corrupt-org")
        org_id = org.id
        context = _context_for(org_id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()
        row = ic.get_integration(session, org_id, IntegrationProvider.WHATSAPP)
        row.encrypted_credentials = row.encrypted_credentials[:-6] + "ZZZZZZ"
        session.commit()

        result = ic.resolve_credentials(session, context, IntegrationProvider.WHATSAPP)
        session.commit()
    assert result is None

    with Session(isolated) as session:
        row = ic.get_integration(session, org_id, IntegrationProvider.WHATSAPP)
        assert row.status == IntegrationStatus.ERROR
        assert row.last_error_code == "decryption_failed"


def test_wrong_provider_never_returns_a_different_providers_credential(isolated) -> None:
    with Session(isolated) as session:
        org = _make_org(session, "wrong-provider-org")
        context = _context_for(org.id)
        ic.configure_integration(session, context, IntegrationProvider.WHATSAPP, secret_fields=FAKE_WHATSAPP_A, configuration_fields={"phone_number_id": "PHONE-A-111"})
        session.commit()
        resolved = ic.resolve_credentials(session, context, IntegrationProvider.GMAIL)
    assert resolved is None
