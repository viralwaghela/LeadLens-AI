"""V2 Phase 9.1 tests: multi-organization credential health + readiness
hardening.

Covers the two confirmed observability defects from the independent
Phase 9 audit:

    1. check_credential_encryption() decided severity from feature-flag
       INTENT rather than whether any real, stored OrganizationIntegration
       row actually needs the key — fixed by
       services.integration_credentials.credential_encryption_key_required()
       and assess_integration_health(), which query actual database state.
    2. check_integration_configuration()/integration_statuses() only
       ever inspected the transitional default organization — fixed by
       assess_integration_health() enumerating every ACTIVE organization.

Also closes a defect found during THIS phase's own adversarial testing:
a syntactically-valid but WRONG encryption key was reported HEALTHY by
the first draft of check_credential_encryption() (it only checked
"is a key present + does data exist requiring one", never "does this
key actually decrypt the data") — fixed by deriving the verdict from a
real, read-only decryption attempt (assess_integration_health()) for
both checks.

Every test uses a temporary/isolated database. import _bootstrap first,
same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

import services.integration_credentials as ic
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.integration import IntegrationProvider
from core.db.session import make_engine
from core.identity import organization_service
from core.identity.tenant_context import ActorType, TenantContext
from services.integration_credentials import (
    assess_integration_health,
    configure_integration,
    credential_encryption_key_required,
)

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def isolated(monkeypatch):
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", False)

    from services import credential_encryption as ce

    ce._fernet_for_version.cache_clear()
    yield engine
    engine.dispose()


@pytest.fixture()
def isolated_file_db(tmp_path, monkeypatch):
    """A real temp-file-backed SQLite database, for tests that exercise
    scripts/health_check.py's functions — each of those internally calls
    make_engine()/dispose() several times per test (once per check), so
    a SHARED in-memory engine object (like the `isolated` fixture above)
    would be disposed out from under itself partway through — the same
    class of bug Phase 9.1's own run_all_checks() fix addressed. A
    file-backed database lets each internal make_engine() call create
    its own independent Engine pointing at the same file, so disposing
    one is safe and never affects the others."""
    db_path = tmp_path / "health_check_test.db"
    url = f"sqlite:///{db_path}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()

    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", False)

    import core.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "get_database_url", lambda: url)
    monkeypatch.setattr(ic, "_ENGINE", None)  # force integration_credentials to re-resolve via the patched URL

    from services import credential_encryption as ce

    ce._fernet_for_version.cache_clear()
    yield url


def _configure(session, org_id: int, provider: IntegrationProvider = IntegrationProvider.WHATSAPP) -> None:
    context = TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM)
    configure_integration(
        session, context, provider,
        secret_fields={"access_token": "test-token-not-real"},
        configuration_fields={"phone_number_id": "123"},
    )


# ---------------------------------------------------------------------------
# 1. services.integration_credentials — the core query functions
# ---------------------------------------------------------------------------

def test_no_credentials_no_key_not_required(isolated, monkeypatch) -> None:
    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with Session(isolated) as session:
        assert credential_encryption_key_required(session) is False


def test_credential_exists_key_present_healthy(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Org A", slug="cred9-a")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    with Session(isolated) as session:
        assert credential_encryption_key_required(session) is True
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.decryptable is True
        assert whatsapp.error_category is None


def test_credential_exists_key_missing_detected(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Org B", slug="cred9-b")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        assert credential_encryption_key_required(session) is True
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.decryptable is False
        assert whatsapp.error_category == "key_unavailable"


def test_credential_exists_wrong_key_detected(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Org C", slug="cred9-c")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())  # different, wrong key
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.decryptable is False
        assert whatsapp.error_category == "decryption_failed"


def test_corrupted_ciphertext_detected(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Org D", slug="cred9-d")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    from core.db.models.integration import OrganizationIntegration

    with Session(isolated) as session:
        row = (
            session.query(OrganizationIntegration)
            .filter(OrganizationIntegration.organization_id == org_id)
            .one()
        )
        row.encrypted_credentials = "not-valid-fernet-ciphertext-at-all"
        session.commit()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.decryptable is False
        assert whatsapp.error_category == "decryption_failed"


def test_org_a_healthy_org_b_broken_only_b_surfaced(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Healthy Org", slug="cred9-healthy")
        org_b = organization_service.create_organization(session, name="Broken Org", slug="cred9-broken")
        session.flush()
        _configure(session, org_a.id)
        _configure(session, org_b.id)
        session.commit()
        a_id, b_id = org_a.id, org_b.id

    from core.db.models.integration import OrganizationIntegration

    with Session(isolated) as session:
        row = (
            session.query(OrganizationIntegration)
            .filter(OrganizationIntegration.organization_id == b_id)
            .one()
        )
        row.encrypted_credentials = "corrupted"
        session.commit()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        a_entry = next(e for e in entries if e.organization_id == a_id and e.provider == "WHATSAPP")
        b_entry = next(e for e in entries if e.organization_id == b_id and e.provider == "WHATSAPP")
        assert a_entry.decryptable is True
        assert b_entry.decryptable is False
        broken = [e for e in entries if e.decryptable is False]
        assert len(broken) == 1
        assert broken[0].organization_slug == "cred9-broken"


def test_non_default_org_only_still_detected(isolated, monkeypatch) -> None:
    """The exact Phase 9 audit scenario: a broken credential belonging
    to a NON-default (non-transitional) organization must still be
    detected — not silently invisible because it isn't the first/only
    organization resolve_default_organization_id() would pick."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        # A "default-clinic" transitional org is resolved separately by
        # other subsystems, but assess_integration_health() must not
        # depend on it — verified here with only non-default orgs.
        org_x = organization_service.create_organization(session, name="Solo Non-Default", slug="cred9-solo")
        session.flush()
        _configure(session, org_x.id)
        session.commit()
        org_id = org_x.id

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        assert any(e.organization_id == org_id and e.decryptable is False for e in entries)


def test_unconfigured_optional_provider_not_flagged_broken(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Sparse Org", slug="cred9-sparse")
        session.commit()
        org_id = org.id

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        org_entries = [e for e in entries if e.organization_id == org_id]
        assert all(e.status == "UNCONFIGURED" for e in org_entries)
        assert all(e.decryptable is None for e in org_entries)  # never falsely "broken"


def test_disabled_integration_not_treated_as_undecryptable(isolated, monkeypatch) -> None:
    """A deliberately DISABLED integration should not attempt/require
    decryption for health purposes — it's intentionally off, not broken."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Disabled Integration Org", slug="cred9-disabled-int")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    from services.integration_credentials import disable_integration

    with Session(isolated) as session:
        disable_integration(session, TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM), IntegrationProvider.WHATSAPP)
        session.commit()

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "DISABLED"
        assert whatsapp.decryptable is None  # not attempted — disabled is intentional, not broken


def test_disabled_organization_excluded_by_default(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Deactivated Org", slug="cred9-deactivated")
        session.flush()
        _configure(session, org.id)
        organization_service.deactivate_organization(session, org.id)
        session.commit()
        org_id = org.id

    with Session(isolated) as session:
        entries = assess_integration_health(session)  # default: ACTIVE organizations only
        assert not any(e.organization_id == org_id for e in entries)

        full_entries = assess_integration_health(session, include_inactive_organizations=True)
        assert any(e.organization_id == org_id for e in full_entries)


def test_no_secret_value_in_output(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Secret Org", slug="cred9-secret")
        session.flush()
        _configure(session, org.id)
        session.commit()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        for e in entries:
            rendered = repr(e)
            assert "test-token-not-real" not in rendered
            assert key not in rendered


# ---------------------------------------------------------------------------
# 2. scripts/health_check.py — end-to-end via monkeypatched engine
# ---------------------------------------------------------------------------

def _patch_health_check_engine(monkeypatch, url):
    """Point core.db.session.get_database_url() at a real temp-file SQLite
    database without overriding make_engine() itself — each internal
    make_engine()/dispose() pair inside scripts/health_check.py then
    constructs and tears down its own independent Engine object pointing
    at the same underlying file, exactly like production. Overriding
    make_engine() to always return one shared Engine object (the earlier
    approach) broke once a check disposed it, corrupting the engine for
    every later check in the same test — see Phase 8.1's identical fix
    to scheduler/run_scheduled_checks.py::run_all_checks() for precedent."""
    import core.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "get_database_url", lambda: url)


def test_health_check_missing_key_with_credentials_is_unhealthy(isolated_file_db, monkeypatch) -> None:
    url = isolated_file_db
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    engine = make_engine(url)
    try:
        with Session(engine) as session:
            org = organization_service.create_organization(session, name="HC Org", slug="cred9-hc")
            session.flush()
            _configure(session, org.id)
            session.commit()
    finally:
        engine.dispose()

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()
    _patch_health_check_engine(monkeypatch, url)

    from scripts.health_check import check_credential_encryption, check_integration_configuration

    key_outcome = check_credential_encryption()
    assert key_outcome.status == "UNHEALTHY"

    integration_outcome = check_integration_configuration()
    assert integration_outcome.status == "UNHEALTHY"
    assert "cred9-hc" in integration_outcome.detail


def test_health_check_no_credentials_healthy_not_required(isolated_file_db, monkeypatch) -> None:
    url = isolated_file_db
    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("LEADLENS_V2_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("LEADLENS_V2_TENANT_CONTEXT_ENABLED", raising=False)
    _patch_health_check_engine(monkeypatch, url)

    from scripts.health_check import check_credential_encryption

    outcome = check_credential_encryption()
    assert outcome.status == "HEALTHY"
    assert "not required" in outcome.detail
