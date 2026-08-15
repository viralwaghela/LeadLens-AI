"""V2 Phase 3 tests: CRM relational dual-write, backfill, repair, parity.

Every test uses its own private, temporary SQLite database (for the V2
relational side) and a private temp directory (for the legacy
core.memory side, via monkeypatching DATABASE_FOLDER — the same
established pattern tests/test_approval_actions.py already uses) —
never the tracked local dev database, never a real DATABASE_URL. import
_bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import threading
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

import core.memory as business_memory
import services.clinic_data_service as crm
import services.relational_sync_service as rs
import scripts.backfill_v2_crm as backfill_mod
import scripts.repair_v2_crm as repair_mod
import scripts.verify_v2_crm_parity as parity_mod
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.clinic import (
    Appointment,
    CorporateClient,
    Lead,
    Package,
    PackageTemplate,
    Patient,
    Payment,
    ProgressNote,
    Therapist,
)
from core.db.models.shadow_sync import ShadowSyncFailure
from core.db.session import make_engine
from core.identity import organization_service

ORG_SLUG = "phase3-test-clinic"

_MODEL_BY_ENTITY = {
    "therapists": Therapist,
    "patients": Patient,
    "appointments": Appointment,
    "package_templates": PackageTemplate,
    "packages": Package,
    "payments": Payment,
    "progress_notes": ProgressNote,
    "leads": Lead,
    "corporate_clients": CorporateClient,
}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Isolated legacy store (temp DATABASE_FOLDER) + isolated V2 store
    (temp in-memory engine) + dual-write enabled, all restored after the
    test by monkeypatch's automatic teardown."""
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(rs, "_ENGINE", engine)
    monkeypatch.setattr(rs, "DUAL_WRITE_ENABLED", True)
    monkeypatch.setattr(
        "core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", ORG_SLUG
    )
    yield engine
    engine.dispose()


def _relational_row(engine, entity: str, external_id: str):
    model = _MODEL_BY_ENTITY[entity]
    with Session(engine) as session:
        return (
            session.query(model)
            .filter(model.external_id == external_id)
            .one_or_none()
        )


# ---------------------------------------------------------------------------
# Dual-write create — for every supported entity
# ---------------------------------------------------------------------------

def test_dual_write_create_therapist(isolated) -> None:
    row = crm.add_record("therapists", {"name": "Dr Smith", "weekly_capacity": 20})
    relational = _relational_row(isolated, "therapists", row["therapist_id"])
    assert relational is not None
    assert relational.name == "Dr Smith"
    assert relational.weekly_capacity == 20


def test_dual_write_create_patient(isolated) -> None:
    row = crm.add_record("patients", {"name": "Jane Doe", "email": "jane@example.com"})
    relational = _relational_row(isolated, "patients", row["patient_id"])
    assert relational is not None
    assert relational.name == "Jane Doe"
    assert relational.email == "jane@example.com"


def test_dual_write_create_appointment(isolated) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    appt = crm.add_record(
        "appointments",
        {"patient_id": patient["patient_id"], "appointment_date": "2026-03-01", "status": "Scheduled"},
    )
    relational = _relational_row(isolated, "appointments", appt["appointment_id"])
    assert relational is not None
    assert relational.appointment_date.isoformat() == "2026-03-01"
    with Session(isolated) as session:
        patient_row = session.get(Patient, relational.patient_id)
        assert patient_row.external_id == patient["patient_id"]


def test_dual_write_create_package_template(isolated) -> None:
    row = crm.add_record("package_templates", {"name": "10-session pack", "total_sessions": 10, "price": 199.99})
    relational = _relational_row(isolated, "package_templates", row["template_id"])
    assert relational is not None
    assert relational.price == Decimal("199.99")


def test_dual_write_create_package(isolated) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    row = crm.add_record(
        "packages",
        {"patient_id": patient["patient_id"], "name": "10-session pack", "total_sessions": 10, "sessions_remaining": 10},
    )
    relational = _relational_row(isolated, "packages", row["package_id"])
    assert relational is not None
    assert relational.sessions_remaining == 10


def test_dual_write_create_payment(isolated) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    row = crm.add_record(
        "payments",
        {"patient_id": patient["patient_id"], "amount": 1234.56, "payment_date": "2026-03-01"},
    )
    relational = _relational_row(isolated, "payments", row["payment_id"])
    assert relational is not None
    assert relational.amount == Decimal("1234.56")


def test_dual_write_create_progress_note(isolated) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    row = crm.add_record(
        "progress_notes",
        {"patient_id": patient["patient_id"], "visit_date": "2026-03-01", "progress_summary": "Doing well."},
    )
    relational = _relational_row(isolated, "progress_notes", row["progress_id"])
    assert relational is not None
    assert relational.progress_summary == "Doing well."


def test_dual_write_create_lead(isolated) -> None:
    row = crm.add_record("leads", {"name": "A Lead", "source": "Website"})
    relational = _relational_row(isolated, "leads", row["lead_id"])
    assert relational is not None
    assert relational.name == "A Lead"


def test_dual_write_create_corporate_client(isolated) -> None:
    row = crm.add_record("corporate_clients", {"company_name": "Acme Corp"})
    relational = _relational_row(isolated, "corporate_clients", row["client_id"])
    assert relational is not None
    assert relational.company_name == "Acme Corp"


# ---------------------------------------------------------------------------
# Dual-write update
# ---------------------------------------------------------------------------

def test_dual_write_update_matches_final_legacy_state(isolated) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe", "status": "Active", "sessions_remaining": 5})
    crm.update_record("patients", patient["patient_id"], {"status": "Inactive", "sessions_remaining": 1})

    legacy = crm.get_record("patients", patient["patient_id"])
    relational = _relational_row(isolated, "patients", patient["patient_id"])
    assert relational.status.value == legacy["status"]
    assert relational.sessions_remaining == legacy["sessions_remaining"] == 1

    # No duplicate row was created.
    with Session(isolated) as session:
        count = session.query(Patient).filter(Patient.external_id == patient["patient_id"]).count()
    assert count == 1


# ---------------------------------------------------------------------------
# Archive/delete semantics — status-flag based, no hard delete
# ---------------------------------------------------------------------------

def test_archive_sets_status_not_hard_delete(isolated) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    crm.archive_record("patients", patient["patient_id"])

    relational = _relational_row(isolated, "patients", patient["patient_id"])
    assert relational is not None  # still exists — archived, not deleted
    assert relational.status.value == "Archived"


# ---------------------------------------------------------------------------
# Failure isolation — legacy succeeds even when the shadow write fails
# ---------------------------------------------------------------------------

def test_missing_parent_failure_does_not_break_legacy_write(isolated) -> None:
    # An appointment for a patient that was never itself dual-written
    # (simulated by inserting it directly into the legacy store without
    # going through crm.add_record, so no relational Patient row exists).
    rows = crm._read_rows("patients")
    rows.append({"patient_id": "P-999", "name": "Ghost Patient", "status": "Active", "created_at": "x", "updated_at": "x"})
    crm.save_records("patients", rows)

    appt = crm.add_record(
        "appointments", {"patient_id": "P-999", "appointment_date": "2026-03-01", "status": "Scheduled"}
    )
    # Legacy write succeeded regardless.
    assert crm.get_record("appointments", appt["appointment_id"]) is not None

    with Session(isolated) as session:
        failures = session.query(ShadowSyncFailure).filter(ShadowSyncFailure.entity == "appointments").all()
    assert len(failures) == 1
    assert failures[0].error_category == "missing_parent"
    assert failures[0].external_id == appt["appointment_id"]

    # Repairable once the parent exists.
    crm.add_record("patients", {"patient_id": "P-999-real", "name": "Real sync"})  # unrelated sanity write
    patient_real = crm.add_record("patients", {"name": "Now synced"})
    crm.update_record("appointments", appt["appointment_id"], {"patient_id": patient_real["patient_id"]})
    relational = _relational_row(isolated, "appointments", appt["appointment_id"])
    assert relational is not None


def test_db_unavailable_does_not_crash_legacy_write(isolated, tmp_path) -> None:
    broken_engine = make_engine("sqlite:///:memory:")  # no create_all() -> every query fails
    rs._ENGINE = broken_engine
    try:
        row = crm.add_record("leads", {"name": "Still saved"})  # must not raise
        assert crm.get_record("leads", row["lead_id"]) is not None
    finally:
        broken_engine.dispose()


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

def test_kill_switch_disables_relational_writes(isolated, monkeypatch) -> None:
    monkeypatch.setattr(rs, "DUAL_WRITE_ENABLED", False)
    row = crm.add_record("patients", {"name": "No shadow write"})
    assert crm.get_record("patients", row["patient_id"]) is not None  # legacy still works
    with Session(isolated) as session:
        assert session.query(Patient).count() == 0
        assert session.query(ShadowSyncFailure).count() == 0


def test_dual_write_default_is_off() -> None:
    """No test fixture here deliberately — proves the module's own
    import-time default (not a fixture override) is OFF, matching the
    deployment sequence in docs/V2_PHASE3_CRM_DUAL_WRITE.md."""
    import os

    assert os.getenv("LEADLENS_V2_DUAL_WRITE_ENABLED", "").strip().lower() not in {"1", "true", "yes"}
    assert rs.DUAL_WRITE_ENABLED is False


# ---------------------------------------------------------------------------
# Organization isolation
# ---------------------------------------------------------------------------

def test_same_external_id_different_organizations_does_not_collide(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A", slug="org-a")
        org_b = organization_service.create_organization(session, name="Org B", slug="org-b")
        rs.sync_one(session, org_a.id, "leads", {"lead_id": "L-SAME", "name": "For org A", "source": "Website", "status": "New"})
        rs.sync_one(session, org_b.id, "leads", {"lead_id": "L-SAME", "name": "For org B", "source": "Website", "status": "New"})
        session.commit()

    with Session(isolated) as session:
        rows = session.query(Lead).filter(Lead.external_id == "L-SAME").all()
        assert len(rows) == 2
        assert {r.name for r in rows} == {"For org A", "For org B"}


def test_cross_tenant_parent_lookup_rejected(isolated) -> None:
    """A patient synced under Org A must not be resolvable as an
    appointment's parent when syncing under Org B — proves
    _resolve_parent_id is org-scoped, not global."""
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A", slug="org-a-2")
        org_b = organization_service.create_organization(session, name="Org B", slug="org-b-2")
        rs.sync_one(session, org_a.id, "patients", {"patient_id": "P-001", "name": "Org A patient", "status": "Active"})
        session.commit()

        with pytest.raises(rs.MissingParentError):
            rs.sync_one(
                session, org_b.id, "appointments",
                {"appointment_id": "A-001", "patient_id": "P-001", "appointment_date": "2026-03-01", "status": "Scheduled"},
            )


def test_cross_tenant_optional_parent_rejected_not_silently_dropped(isolated) -> None:
    """Regression test for a bug found during the Phase 3 audit: an
    OPTIONAL parent reference (therapist on appointments/progress_notes,
    package on payments) that is supplied but only resolves in a
    DIFFERENT organization used to silently resolve to None instead of
    raising — masking a cross-tenant data problem as a quiet success.
    `required=False` must only permit a MISSING id, never an
    unresolvable-but-supplied one."""
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A", slug="org-a-3")
        org_b = organization_service.create_organization(session, name="Org B", slug="org-b-3")
        rs.sync_one(session, org_a.id, "therapists", {"therapist_id": "T-001", "name": "Org A therapist", "status": "Active"})
        rs.sync_one(session, org_b.id, "patients", {"patient_id": "P-B", "name": "Org B patient", "status": "Active"})
        session.commit()

        with pytest.raises(rs.MissingParentError):
            rs.sync_one(
                session, org_b.id, "progress_notes",
                {
                    "progress_id": "PRG-001", "patient_id": "P-B", "therapist_id": "T-001",
                    "visit_date": "2026-03-01", "progress_summary": "x",
                },
            )
        with pytest.raises(rs.MissingParentError):
            rs.sync_one(
                session, org_b.id, "appointments",
                {
                    "appointment_id": "A-002", "patient_id": "P-B", "therapist_id": "T-001",
                    "appointment_date": "2026-03-01", "status": "Scheduled",
                },
            )

    # But a genuinely-absent optional parent (empty string / None) is still fine.
    with Session(isolated) as session:
        org_c = organization_service.create_organization(session, name="Org C", slug="org-c-3")
        rs.sync_one(session, org_c.id, "patients", {"patient_id": "P-C", "name": "Org C patient", "status": "Active"})
        rs.sync_one(
            session, org_c.id, "appointments",
            {"appointment_id": "A-003", "patient_id": "P-C", "therapist_id": "", "appointment_date": "2026-03-01", "status": "Scheduled"},
        )  # must not raise


# ---------------------------------------------------------------------------
# Financial parity
# ---------------------------------------------------------------------------

def test_payment_amount_exact_decimal_no_float_drift(isolated) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    row = crm.add_record(
        "payments", {"patient_id": patient["patient_id"], "amount": 19.1, "payment_date": "2026-03-01"}
    )
    relational = _relational_row(isolated, "payments", row["payment_id"])
    # 19.1 is not exactly representable in binary float; going through
    # Decimal(str(x)) must still preserve the intended decimal value.
    assert relational.amount == Decimal("19.1")
    assert str(relational.amount) == "19.10" or str(relational.amount) == "19.1"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_concurrent_syncs_do_not_duplicate_or_lose_state(isolated, tmp_path) -> None:
    # File-based (not :memory:) so multiple threads share real state.
    file_engine = make_engine(f"sqlite:///{tmp_path / 'concurrency.db'}")
    Base.metadata.create_all(file_engine)
    rs._ENGINE = file_engine
    try:
        with Session(file_engine) as session:
            org_id = organization_service.create_organization(session, name="Concurrency Org", slug="concurrency-org").id
            session.commit()

        errors: list[Exception] = []

        def _write(name: str) -> None:
            try:
                with Session(file_engine) as session:
                    rs.sync_one(session, org_id, "leads", {"lead_id": "L-RACE", "name": name, "source": "Website", "status": "New"})
                    session.commit()
            except Exception as exc:  # noqa: BLE001 - recorded, not re-raised, matching live behavior
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(f"Attempt {i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with Session(file_engine) as session:
            rows = session.query(Lead).filter(Lead.external_id == "L-RACE").all()
        assert len(rows) == 1  # never duplicated, regardless of how many threads raced
    finally:
        file_engine.dispose()


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def test_backfill_empty_clinic(isolated) -> None:
    report = backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated)
    for counts in report["by_entity"].values():
        assert counts["legacy_rows"] == 0
        assert counts["created"] == 0


def test_backfill_complete(isolated) -> None:
    # Create legacy data WITHOUT dual-write (simulating pre-Phase-3 history).
    rs.DUAL_WRITE_ENABLED = False
    patient = crm.add_record("patients", {"name": "Backfill Patient"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-04-01", "status": "Scheduled"})
    crm.add_record("leads", {"name": "Backfill Lead"})
    rs.DUAL_WRITE_ENABLED = True

    with Session(isolated) as session:
        assert session.query(Patient).count() == 0  # confirmed not yet synced

    report = backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated)
    assert report["by_entity"]["patients"]["created"] == 1
    assert report["by_entity"]["appointments"]["created"] == 1
    assert report["by_entity"]["leads"]["created"] == 1

    with Session(isolated) as session:
        assert session.query(Patient).filter(Patient.external_id == patient["patient_id"]).count() == 1


def test_backfill_is_idempotent(isolated) -> None:
    rs.DUAL_WRITE_ENABLED = False
    crm.add_record("leads", {"name": "Lead One"})
    rs.DUAL_WRITE_ENABLED = True

    backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated)
    second = backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated)
    assert second["by_entity"]["leads"]["created"] == 0
    assert second["by_entity"]["leads"]["updated"] == 1

    with Session(isolated) as session:
        assert session.query(Lead).count() == 1


def test_backfill_relational_record_already_exists_gets_updated_not_duplicated(isolated) -> None:
    row = crm.add_record("leads", {"name": "Original", "status": "New"})  # dual-write already synced it
    crm.update_record("leads", row["lead_id"], {"status": "Contacted"})

    report = backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated)
    assert report["by_entity"]["leads"]["updated"] == 1
    assert report["by_entity"]["leads"]["created"] == 0

    with Session(isolated) as session:
        lead = session.query(Lead).filter(Lead.external_id == row["lead_id"]).one()
        assert lead.status.value == "Contacted"


def test_backfill_reports_malformed_legacy_record_without_crashing(isolated) -> None:
    rs.DUAL_WRITE_ENABLED = False
    rows = crm._read_rows("patients")
    rows.append({"patient_id": "", "name": "No id at all"})  # malformed: empty id
    crm.save_records("patients", rows)
    rs.DUAL_WRITE_ENABLED = True

    report = backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated, entities=("patients",))
    # The malformed row (no patient_id) fails validation inside sync_one()
    # and is recorded as a failure — it does not crash the whole backfill,
    # and it is not silently counted as a success.
    counts = report["by_entity"]["patients"]
    assert counts["legacy_rows"] == 1
    assert counts["created"] == 0
    assert counts["failed"] == 1

    with Session(isolated) as session:
        failures = session.query(ShadowSyncFailure).filter(ShadowSyncFailure.entity == "patients").all()
    assert len(failures) == 1
    assert failures[0].error_category == "validation"


def test_backfill_relationship_ordering_resolves_within_one_run(isolated) -> None:
    rs.DUAL_WRITE_ENABLED = False
    patient = crm.add_record("patients", {"name": "Order Test"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-05-01", "status": "Scheduled"})
    rs.DUAL_WRITE_ENABLED = True

    report = backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated)
    assert report["by_entity"]["patients"]["created"] == 1
    assert report["by_entity"]["appointments"]["created"] == 1  # patient synced first, so this resolves


def test_backfill_dry_run_writes_nothing(isolated) -> None:
    rs.DUAL_WRITE_ENABLED = False
    crm.add_record("leads", {"name": "Should not be written"})
    rs.DUAL_WRITE_ENABLED = True

    backfill_mod.backfill(org_slug=ORG_SLUG, engine=isolated, dry_run=True)
    with Session(isolated) as session:
        assert session.query(Lead).count() == 0


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def test_repair_resyncs_and_resolves_failure(isolated) -> None:
    rows = crm._read_rows("patients")
    rows.append({"patient_id": "P-GHOST", "name": "Ghost", "status": "Active", "created_at": "x", "updated_at": "x"})
    crm.save_records("patients", rows)
    appt = crm.add_record("appointments", {"patient_id": "P-GHOST", "appointment_date": "2026-03-01", "status": "Scheduled"})

    with Session(isolated) as session:
        assert session.query(ShadowSyncFailure).filter(ShadowSyncFailure.resolved.is_(False)).count() == 1

    # Now the "ghost" patient actually gets synced (e.g. a later repair run of patients).
    with Session(isolated) as session:
        org_id = organization_service.get_organization_by_slug(session, ORG_SLUG).id
        rs.sync_one(session, org_id, "patients", crm.get_record("patients", "P-GHOST"))
        session.commit()

    ok = repair_mod.repair_record(org_slug=ORG_SLUG, entity="appointments", record_id=appt["appointment_id"], engine=isolated)
    assert ok is True

    with Session(isolated) as session:
        assert session.query(ShadowSyncFailure).filter(ShadowSyncFailure.resolved.is_(False)).count() == 0


def test_repair_is_idempotent(isolated) -> None:
    row = crm.add_record("leads", {"name": "Fine already"})
    ok1 = repair_mod.repair_record(org_slug=ORG_SLUG, entity="leads", record_id=row["lead_id"], engine=isolated)
    ok2 = repair_mod.repair_record(org_slug=ORG_SLUG, entity="leads", record_id=row["lead_id"], engine=isolated)
    assert ok1 is True and ok2 is True
    with Session(isolated) as session:
        assert session.query(Lead).filter(Lead.external_id == row["lead_id"]).count() == 1


# ---------------------------------------------------------------------------
# Parity verification
# ---------------------------------------------------------------------------

def test_parity_verify_passes_when_synced(isolated) -> None:
    crm.add_record("leads", {"name": "Synced Lead", "status": "New"})
    report = parity_mod.verify_parity(org_slug=ORG_SLUG, engine=isolated)
    assert report["ok"] is True
    assert report["by_entity"]["leads"]["matched"] == 1
    assert report["by_entity"]["leads"]["mismatch"] == 0


def test_parity_verify_detects_missing_relational_row(isolated) -> None:
    # A first record syncs normally (so the organization exists in the
    # relational store), then dual-write is disabled and a second record
    # is added that never gets synced — the realistic "temporarily
    # disabled" gap this check exists to catch.
    crm.add_record("leads", {"name": "Synced fine"})
    rs.DUAL_WRITE_ENABLED = False
    crm.add_record("leads", {"name": "Never synced"})
    rs.DUAL_WRITE_ENABLED = True

    report = parity_mod.verify_parity(org_slug=ORG_SLUG, engine=isolated)
    assert report["ok"] is False
    assert report["by_entity"]["leads"]["missing_from_relational"]
    assert report["by_entity"]["leads"]["matched"] == 1


def test_parity_verify_detects_status_mismatch(isolated) -> None:
    row = crm.add_record("leads", {"name": "Drifted", "status": "New"})
    with Session(isolated) as session:
        lead = session.query(Lead).filter(Lead.external_id == row["lead_id"]).one()
        lead.status = __import__("core.db.models.clinic", fromlist=["LeadStatus"]).LeadStatus.CONTACTED
        session.commit()

    report = parity_mod.verify_parity(org_slug=ORG_SLUG, engine=isolated)
    assert report["ok"] is False
    assert row["lead_id"] in report["by_entity"]["leads"]["mismatched_ids"]
