"""V2 Phase 9.1.1 tests: ERROR-state credential health classification.

Closes the one confirmed defect from the independent Phase 9.1 audit:
assess_integration_health() only attempted a decryption-based health
classification for status == ACTIVE rows. But resolve_credentials() (the
real adapter read path) transitions a row to status == ERROR the moment
a LIVE decryption failure occurs — after that transition, the row fell
out of the health check's classify branch entirely and was reported
decryptable=None / error_category=None, identical to an intentionally
DISABLED integration. check_credential_encryption(),
check_integration_configuration(), and production_readiness.py's
integration_credentials section could all report HEALTHY/PASS for a
credential already known broken in live use.

Fix: assess_integration_health() now decrypt-attempts ERROR-status rows
exactly like ACTIVE ones (services/integration_credentials.py's
resolve_credentials() is the only place that ever sets ERROR, and always
after a real decryption failure — this module makes no live API calls,
so there is no "ERROR from provider connectivity" case to conflate with
a credential problem in this codebase today). Still fully read-only:
assess_integration_health() never calls session.flush()/commit(), never
rewrites status/last_error/ciphertext, never re-activates a row.

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
from core.db.models.integration import IntegrationProvider, IntegrationStatus, OrganizationIntegration
from core.db.session import make_engine
from core.identity import organization_service
from core.identity.tenant_context import ActorType, TenantContext
from services.integration_credentials import (
    assess_integration_health,
    configure_integration,
    resolve_credentials,
)


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


def _configure(session, org_id: int, provider: IntegrationProvider = IntegrationProvider.WHATSAPP) -> None:
    context = TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM)
    configure_integration(
        session, context, provider,
        secret_fields={"access_token": "test-token-not-real"},
        configuration_fields={"phone_number_id": "123"},
    )


def _force_error_via_live_resolution(engine, org_id: int, provider: IntegrationProvider = IntegrationProvider.WHATSAPP) -> None:
    """Exercises the REAL production path: a live resolve_credentials()
    call against a currently-undecryptable row, which transitions the
    row to status ERROR — exactly what an actual failed WhatsApp/Gmail/
    Calendar send would do. Deliberately not a direct ORM status write,
    so this proves the fix works against the genuine state transition,
    not a synthetic one."""
    with Session(engine) as session:
        context = TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM)
        result = resolve_credentials(session, context, provider)
        session.commit()
        assert result is None, "resolve_credentials() should fail closed when the row cannot be decrypted"
    with Session(engine) as session:
        row = (
            session.query(OrganizationIntegration)
            .filter(OrganizationIntegration.organization_id == org_id, OrganizationIntegration.provider == provider)
            .one()
        )
        assert row.status == IntegrationStatus.ERROR


# ---------------------------------------------------------------------------
# 1. The exact audit regression scenario (must fail before the fix, pass after)
# ---------------------------------------------------------------------------

def test_error_state_credential_surfaced_across_all_three_surfaces(isolated, monkeypatch) -> None:
    """Reproduces the Phase 9.1 audit's exact adversarial sequence:
    configure -> rotate to a wrong key -> a real resolve_credentials()
    call (simulating a live send attempt) marks the row ERROR -> every
    health/readiness surface must now visibly report the problem."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Audit Regression Org", slug="cred911-audit")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    # Rotate to a different (wrong) key, then trigger a REAL live resolution
    # failure — this is what actually sets status=ERROR in production.
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ce._fernet_for_version.cache_clear()
    _force_error_via_live_resolution(isolated, org_id)

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "ERROR"
        assert whatsapp.decryptable is False, "an ERROR-status row with bad ciphertext must be surfaced as unhealthy"
        assert whatsapp.error_category == "decryption_failed"


# ---------------------------------------------------------------------------
# 2. ERROR-state classification, all failure modes
# ---------------------------------------------------------------------------

def test_error_state_broken_ciphertext_surfaced(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Corrupt Then Error Org", slug="cred911-corrupt")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    with Session(isolated) as session:
        row = session.query(OrganizationIntegration).filter(OrganizationIntegration.organization_id == org_id).one()
        row.encrypted_credentials = "not-valid-fernet-ciphertext-at-all"
        session.commit()

    _force_error_via_live_resolution(isolated, org_id)

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "ERROR"
        assert whatsapp.decryptable is False
        assert whatsapp.error_category == "decryption_failed"


def test_error_state_missing_key_surfaced(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Missing Key Error Org", slug="cred911-missingkey")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()
    _force_error_via_live_resolution(isolated, org_id)

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "ERROR"
        assert whatsapp.decryptable is False
        assert whatsapp.error_category == "key_unavailable"


def test_error_state_wrong_key_surfaced(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Wrong Key Error Org", slug="cred911-wrongkey")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ce._fernet_for_version.cache_clear()
    _force_error_via_live_resolution(isolated, org_id)

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "ERROR"
        assert whatsapp.decryptable is False
        assert whatsapp.error_category == "decryption_failed"


def test_error_state_fixed_key_reports_decryptable_but_status_stays_error(isolated, monkeypatch) -> None:
    """A non-decryption-caused ERROR is not reproducible in this codebase
    today (resolve_credentials() is the only ERROR writer, and it only
    ever fires on a real decryption failure — see the module docstring).
    The closest coherent stand-in for spec section 5's "not every ERROR
    necessarily means bad encryption" is: an ERROR row whose key has
    SINCE been fixed (an admin corrected the environment's master key)
    but who has not yet re-configured the integration to clear the
    administrative ERROR status. Health assessment must not conflate the
    two: it must report the CURRENT decryptability truthfully
    (decryptable=True — the ciphertext genuinely decrypts under today's
    key) while leaving the row's administrative `status` exactly as
    stored (still "ERROR" — assess_integration_health() never writes
    back, only configure_integration()/disable_integration() may change
    administrative status)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Fixed Key Org", slug="cred911-fixedkey")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ce._fernet_for_version.cache_clear()
    _force_error_via_live_resolution(isolated, org_id)

    # The key is fixed back to the original — but nobody has called
    # configure_integration() again, so the row is still administratively
    # ERROR in the database.
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        row = session.query(OrganizationIntegration).filter(OrganizationIntegration.organization_id == org_id).one()
        assert row.status == IntegrationStatus.ERROR  # unchanged by the key fix alone

        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "ERROR"          # administrative status: untouched
        assert whatsapp.decryptable is True         # but the credential itself decrypts fine right now
        assert whatsapp.error_category is None

        # And assess_integration_health() must not have silently "fixed" the row.
        row_after = session.query(OrganizationIntegration).filter(OrganizationIntegration.organization_id == org_id).one()
        assert row_after.status == IntegrationStatus.ERROR


# ---------------------------------------------------------------------------
# 3. Unchanged behavior for every other status (regression guard)
# ---------------------------------------------------------------------------

def test_active_healthy_unchanged(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Active Healthy Org", slug="cred911-active-ok")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "ACTIVE"
        assert whatsapp.decryptable is True
        assert whatsapp.error_category is None


def test_active_broken_unchanged(isolated, monkeypatch) -> None:
    """An ACTIVE row that has never actually been used live (so it never
    transitioned to ERROR) must still be caught exactly as Phase 9.1 did."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Active Broken Org", slug="cred911-active-broken")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "ACTIVE"
        assert whatsapp.decryptable is False
        assert whatsapp.error_category == "key_unavailable"


def test_disabled_unchanged(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    from services.integration_credentials import disable_integration

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Disabled Unchanged Org", slug="cred911-disabled")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    with Session(isolated) as session:
        disable_integration(session, TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM), IntegrationProvider.WHATSAPP)
        session.commit()

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        whatsapp = next(e for e in entries if e.organization_id == org_id and e.provider == "WHATSAPP")
        assert whatsapp.status == "DISABLED"
        assert whatsapp.decryptable is None
        assert whatsapp.error_category is None


def test_unconfigured_unchanged(isolated) -> None:
    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Unconfigured Unchanged Org", slug="cred911-unconfigured")
        session.commit()
        org_id = org.id

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        org_entries = [e for e in entries if e.organization_id == org_id]
        assert all(e.status == "UNCONFIGURED" for e in org_entries)
        assert all(e.decryptable is None for e in org_entries)


# ---------------------------------------------------------------------------
# 4. Multi-org: only the broken org is flagged
# ---------------------------------------------------------------------------

def test_non_default_org_error_state_surfaced(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Non-Default Error Org", slug="cred911-nondefault")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    ce._fernet_for_version.cache_clear()
    _force_error_via_live_resolution(isolated, org_id)

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        assert any(e.organization_id == org_id and e.status == "ERROR" and e.decryptable is False for e in entries)


def test_multiple_orgs_only_error_org_flagged(isolated, monkeypatch) -> None:
    """The master encryption key is process-wide (one env var), so two
    organizations can't be under "different keys" at once — to get a
    genuinely still-broken org B alongside a genuinely still-healthy org
    A under the SAME current key, org B's stored ciphertext is corrupted
    directly (like the Phase 9.1 corrupted-ciphertext scenario) rather
    than via a key rotation, and the key is never changed."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org_healthy = organization_service.create_organization(session, name="Multi Healthy Org", slug="cred911-multi-healthy")
        org_error = organization_service.create_organization(session, name="Multi Error Org", slug="cred911-multi-error")
        session.flush()
        _configure(session, org_healthy.id)
        _configure(session, org_error.id)
        session.commit()
        healthy_id, error_id = org_healthy.id, org_error.id

    with Session(isolated) as session:
        row = session.query(OrganizationIntegration).filter(OrganizationIntegration.organization_id == error_id).one()
        row.encrypted_credentials = "not-valid-fernet-ciphertext-at-all"
        session.commit()

    _force_error_via_live_resolution(isolated, error_id)

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        healthy_entry = next(e for e in entries if e.organization_id == healthy_id and e.provider == "WHATSAPP")
        error_entry = next(e for e in entries if e.organization_id == error_id and e.provider == "WHATSAPP")

        assert healthy_entry.status == "ACTIVE"
        assert healthy_entry.decryptable is True

        assert error_entry.status == "ERROR"
        assert error_entry.decryptable is False

        broken = [e for e in entries if e.decryptable is False]
        assert len(broken) == 1
        assert broken[0].organization_slug == "cred911-multi-error"


# ---------------------------------------------------------------------------
# 5. Read-only guarantee
# ---------------------------------------------------------------------------

def test_health_check_does_not_mutate_error_row(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Mutation Guard Org", slug="cred911-mutation-guard")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ce._fernet_for_version.cache_clear()
    _force_error_via_live_resolution(isolated, org_id)

    with Session(isolated) as session:
        row_before = session.query(OrganizationIntegration).filter(OrganizationIntegration.organization_id == org_id).one()
        before = {
            "status": row_before.status,
            "encrypted_credentials": row_before.encrypted_credentials,
            "encryption_key_version": row_before.encryption_key_version,
            "last_error_at": row_before.last_error_at,
            "last_error_code": row_before.last_error_code,
        }

    # Call the health assessment (and the DISABLED-intent variant too) several times.
    with Session(isolated) as session:
        assess_integration_health(session)
        assess_integration_health(session, include_inactive_organizations=True)
        assess_integration_health(session)

    with Session(isolated) as session:
        row_after = session.query(OrganizationIntegration).filter(OrganizationIntegration.organization_id == org_id).one()
        after = {
            "status": row_after.status,
            "encrypted_credentials": row_after.encrypted_credentials,
            "encryption_key_version": row_after.encryption_key_version,
            "last_error_at": row_after.last_error_at,
            "last_error_code": row_after.last_error_code,
        }
        assert after == before


# ---------------------------------------------------------------------------
# 6. Output safety
# ---------------------------------------------------------------------------

def test_no_secret_value_in_error_state_output(isolated, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    with Session(isolated) as session:
        org = organization_service.create_organization(session, name="Secret Safety Error Org", slug="cred911-secret-safety")
        session.flush()
        _configure(session, org.id)
        session.commit()
        org_id = org.id

    wrong_key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", wrong_key)
    ce._fernet_for_version.cache_clear()
    _force_error_via_live_resolution(isolated, org_id)

    with Session(isolated) as session:
        entries = assess_integration_health(session)
        for e in entries:
            rendered = repr(e)
            assert "test-token-not-real" not in rendered
            assert key not in rendered
            assert wrong_key not in rendered


# ---------------------------------------------------------------------------
# 7. End-to-end via scripts/health_check.py + scripts/production_readiness.py
# ---------------------------------------------------------------------------

def _patch_health_check_engine(monkeypatch, url):
    import core.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "get_database_url", lambda: url)


def test_health_check_and_readiness_surface_error_state(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "phase911_health_check.db"
    url = f"sqlite:///{db_path}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()

    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", False)
    import core.db.session as db_session_mod
    monkeypatch.setattr(db_session_mod, "get_database_url", lambda: url)
    monkeypatch.setattr(ic, "_ENGINE", None)

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", key)
    from services import credential_encryption as ce
    ce._fernet_for_version.cache_clear()

    engine = make_engine(url)
    try:
        with Session(engine) as session:
            org = organization_service.create_organization(session, name="HC ERR Org", slug="cred911-hc-err")
            session.flush()
            _configure(session, org.id)
            session.commit()
            org_id = org.id
    finally:
        engine.dispose()

    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ce._fernet_for_version.cache_clear()

    engine = make_engine(url)
    try:
        with Session(engine) as session:
            context = TenantContext(organization_id=org_id, actor_type=ActorType.SYSTEM)
            result = resolve_credentials(session, context, IntegrationProvider.WHATSAPP)
            session.commit()
            assert result is None
    finally:
        engine.dispose()

    from scripts.health_check import check_credential_encryption, check_integration_configuration
    from scripts.production_readiness import run_production_readiness

    key_outcome = check_credential_encryption()
    assert key_outcome.status == "UNHEALTHY"

    integration_outcome = check_integration_configuration()
    assert integration_outcome.status == "UNHEALTHY"
    assert "cred911-hc-err" in integration_outcome.detail

    overall, results = run_production_readiness()
    section_status, section_lines = results["integration_credentials"]
    assert section_status == "FAIL"
    assert any("cred911-hc-err" in line for line in section_lines)
