"""V2 Phase 9 tests: production hardening.

Covers config validation, health checks, the production-readiness
aggregator, the appointment_reminder two-org credential-resolution fix,
and the marketing-site tenant-aware lead endpoint. Every test uses a
temporary/isolated database or mocked connection — never the real
DATABASE_URL a developer's .env might point at. import _bootstrap first,
same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import datetime
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
import streamlit as st
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

import core.memory as business_memory
import services.clinic_data_service as crm
import services.crm_read_router as crm_router
import services.integration_credentials as ic
import services.relational_sync_service as rs
import services.tenant_operational_sync as tos
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole
from core.db.models.integration import IntegrationProvider
from core.db.session import make_engine
from core.identity import membership_service, organization_service, user_service
from core.identity.tenant_context import ActorType, TenantContext
from services.appointment_messaging import send_appointment_rsvp_reminder
from services.integration_credentials import configure_integration

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(ic, "_ENGINE", engine)
    monkeypatch.setattr(crm_router, "_ENGINE", engine)
    monkeypatch.setattr(rs, "_ENGINE", engine)
    monkeypatch.setattr(tos, "_ENGINE", engine)
    monkeypatch.setenv("LEADLENS_V2_TENANT_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED", "true")
    monkeypatch.setattr(crm_router, "TENANT_AUTHORITATIVE_ENABLED", True)

    from services import credential_encryption as ce

    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY_VERSION", raising=False)
    monkeypatch.setattr(ic, "ENV_FALLBACK_ENABLED", False)
    ce._fernet_for_version.cache_clear()

    st.session_state.clear()
    yield engine
    st.session_state.clear()
    engine.dispose()


@dataclass
class Clinic:
    org_id: int
    org_name: str


def _provision(session, *, slug: str, name: str) -> Clinic:
    org = organization_service.create_organization(session, name=name, slug=slug)
    user = user_service.create_user(session, email=f"owner@{slug}.example", password=PASSWORD)
    membership_service.create_membership(session, user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER)
    session.commit()
    return Clinic(org_id=org.id, org_name=org.name)


# ---------------------------------------------------------------------------
# 1. CONFIG VALIDATION
# ---------------------------------------------------------------------------

def test_config_validation_flags_open_door_when_no_auth_configured(monkeypatch) -> None:
    from core.config_validation import validate_configuration

    monkeypatch.delenv("LEADLENS_V2_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    report = validate_configuration()
    assert report.has_fail
    assert any("open to anyone" in f.message for f in report.findings)


def test_config_validation_ok_with_legacy_password(monkeypatch) -> None:
    from core.config_validation import validate_configuration

    monkeypatch.delenv("LEADLENS_V2_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("APP_PASSWORD", "some-password")
    report = validate_configuration()
    assert not report.has_fail


def test_config_validation_fails_on_multi_org_without_audit_scoping(monkeypatch) -> None:
    from core.config_validation import validate_configuration

    monkeypatch.setenv("LEADLENS_V2_AUTH_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_AUTH_SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    monkeypatch.delenv("LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED", raising=False)
    report = validate_configuration()
    assert report.has_fail
    assert any("audit" in f.message.lower() for f in report.findings if f.level == "FAIL")


def test_config_validation_never_prints_secret_values(monkeypatch) -> None:
    from core.config_validation import validate_configuration

    secret = "sk-not-a-real-secret-do-not-print-1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    report = validate_configuration()
    assert not any(secret in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# 2. HEALTH CHECK
# ---------------------------------------------------------------------------

def test_health_check_reports_overall_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from scripts.health_check import run_health_check

    report = run_health_check()
    assert report.overall in ("HEALTHY", "DEGRADED", "UNHEALTHY")
    assert any(o.name == "database_connectivity" for o in report.outcomes)


def test_health_check_never_prints_secret_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    secret = "sk-not-a-real-secret-do-not-print-1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    from scripts.health_check import run_health_check

    report = run_health_check()
    assert not any(secret in o.detail for o in report.outcomes)


# ---------------------------------------------------------------------------
# 3. PRODUCTION READINESS AGGREGATOR
# ---------------------------------------------------------------------------

def test_production_readiness_aggregates_all_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from scripts.production_readiness import run_production_readiness

    overall, results = run_production_readiness()
    assert overall in ("PASS", "WARN", "FAIL")
    assert set(results.keys()) == {"configuration", "health", "migration_drift", "tenant_integrity"}
    for level, _lines in results.values():
        assert level in ("PASS", "WARN", "FAIL")


# ---------------------------------------------------------------------------
# 4. APPOINTMENT REMINDER — org-scoped auto-send credential resolution
# ---------------------------------------------------------------------------

def test_appointment_reminder_a_uses_a_credential_b_never_leaks(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="remind9-a", name="Remind9 A")
        clinic_b = _provision(session, slug="remind9-b", name="Remind9 B")

    with Session(isolated) as session:
        configure_integration(
            session, TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SYSTEM),
            IntegrationProvider.WHATSAPP,
            secret_fields={"access_token": "A-TOKEN"}, configuration_fields={"phone_number_id": "a-phone"},
        )
        session.commit()

    crm.add_record("patients", {"name": "Alice", "consent_to_contact": True, "phone": "111"}, organization_id=clinic_a.org_id)
    crm.add_record("patients", {"name": "Bob", "consent_to_contact": True, "phone": "222"}, organization_id=clinic_b.org_id)

    context_a = TenantContext(organization_id=clinic_a.org_id, actor_type=ActorType.SCHEDULER)
    context_b = TenantContext(organization_id=clinic_b.org_id, actor_type=ActorType.SCHEDULER)

    result_a = send_appointment_rsvp_reminder({"patient_id": "P-001"}, context=context_a)
    result_b = send_appointment_rsvp_reminder({"patient_id": "P-001"}, context=context_b)

    assert result_a is not None
    assert result_b is not None
    # B has no configured credential: must dry-run/simulate, never use A's token.
    assert result_b["dry_run"] is True
    assert result_b["status"] == "simulated"


def test_appointment_reminder_patient_lookup_isolated(isolated) -> None:
    with Session(isolated) as session:
        clinic_a = _provision(session, slug="remind9-c", name="Remind9 C")
        clinic_b = _provision(session, slug="remind9-d", name="Remind9 D")

    crm.add_record("patients", {"name": "OnlyInA", "consent_to_contact": True, "phone": "111"}, organization_id=clinic_a.org_id)
    # B has no patients at all.

    context_b = TenantContext(organization_id=clinic_b.org_id, actor_type=ActorType.SCHEDULER)
    result = send_appointment_rsvp_reminder({"patient_id": "P-001"}, context=context_b)
    assert result is None  # B's patient lookup correctly finds nothing — never A's patient


def test_appointment_reminder_no_context_preserves_legacy_behavior(isolated) -> None:
    """Omitting `context` (ui/patient_crm.py's live call site) must not
    raise or change behavior — implicit resolution still applies."""
    crm.add_record("patients", {"name": "Legacy Patient", "consent_to_contact": True, "phone": "333"})
    result = send_appointment_rsvp_reminder({"patient_id": "P-001"})
    assert result is not None


# ---------------------------------------------------------------------------
# 5. MARKETING SITE — relational shadow write
# ---------------------------------------------------------------------------

def test_marketing_site_shadow_write_inserts_scoped_lead() -> None:
    import importlib.util
    from pathlib import Path

    lead_path = Path(__file__).resolve().parents[1] / "marketing-site" / "api" / "lead.py"
    spec = importlib.util.spec_from_file_location("marketing_lead_module_shadow", lead_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    lead = {
        "lead_id": "L-042", "name": "Jane", "phone": "999", "email": "",
        "message": "hi", "source": "Website", "status": "New",
    }
    module._shadow_write_relational_lead(fake_conn, 7, lead, datetime.datetime.now(datetime.timezone.utc))

    fake_cursor.execute.assert_called_once()
    args, _ = fake_cursor.execute.call_args
    sql, params = args
    assert "INSERT INTO leads" in sql
    assert params[0] == 7  # organization_id
    assert params[1] == "L-042"  # external_id
    fake_conn.commit.assert_called_once()


def test_marketing_site_shadow_write_never_raises_on_failure() -> None:
    import importlib.util
    from pathlib import Path

    lead_path = Path(__file__).resolve().parents[1] / "marketing-site" / "api" / "lead.py"
    spec = importlib.util.spec_from_file_location("marketing_lead_module_shadow_fail", lead_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_conn = MagicMock()
    fake_conn.cursor.side_effect = RuntimeError("boom")

    lead = {
        "lead_id": "L-043", "name": "Jane", "phone": "999", "email": "",
        "message": "hi", "source": "Website", "status": "New",
    }
    # Must not raise despite the cursor blowing up.
    module._shadow_write_relational_lead(fake_conn, 7, lead, datetime.datetime.now(datetime.timezone.utc))
    fake_conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# 6. BACKUP / RESTORE
# ---------------------------------------------------------------------------

def test_backup_and_restore_validate_round_trip(tmp_path, monkeypatch) -> None:
    import core.db.session as db_session_mod
    import scripts.backup_database as backup_mod
    import scripts.restore_validate as restore_mod

    relational_path = tmp_path / "relational.db"
    engine = make_engine(f"sqlite:///{relational_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    monkeypatch.setattr(db_session_mod, "DEFAULT_SQLITE_PATH", relational_path)
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "legacy")
    business_memory.ensure_database()

    out_dir = tmp_path / "backups"
    files = backup_mod.run_backup(out_dir, "test")
    assert len(files) == 2
    for f in files:
        assert f.exists()
        assert f.stat().st_size > 0

    relational_backup = next(f for f in files if "relational" in f.name)
    overall, lines = restore_mod.restore_and_validate_sqlite(relational_backup)
    assert overall in ("PASS", "WARN", "FAIL")
    assert any("migration_drift" in line for line in lines)

    # The original backup file must be untouched (restore copies it).
    assert relational_backup.exists()


def test_backup_scrubs_credentials_from_error_messages() -> None:
    from scripts.backup_database import _scrub

    dsn = "postgresql://placeholder:password@db.example.com:5432/leadlens"
    scrubbed = _scrub(f"pg_dump failed: connection to {dsn} refused")
    assert "placeholder:password" not in scrubbed
    assert "<redacted>" in scrubbed
