"""V2 Phase 4 tests: CRM relational read cutover.

Every test uses its own private, temporary SQLite database (V2 side)
and a private temp DATABASE_FOLDER (legacy side, monkeypatched) — never
the tracked local dev database, never a real DATABASE_URL. import
_bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

import core.memory as business_memory
import services.clinic_data_service as crm
import services.crm_read_router as router
import services.relational_sync_service as rs
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.shadow_sync import ReadMismatch
from core.db.session import make_engine
from core.identity import organization_service

ORG_SLUG = "phase4-test-clinic"

ALL_READ_FLAGS = [
    "LEADLENS_V2_READ_LEADS",
    "LEADLENS_V2_READ_CORPORATE_CLIENTS",
    "LEADLENS_V2_READ_SERVICES",
    "LEADLENS_V2_READ_PRACTITIONERS",
    "LEADLENS_V2_READ_PATIENTS",
    "LEADLENS_V2_READ_PACKAGES",
    "LEADLENS_V2_READ_APPOINTMENTS",
    "LEADLENS_V2_READ_PROGRESS_NOTES",
    "LEADLENS_V2_READ_PAYMENTS",
]


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(rs, "_ENGINE", engine)
    monkeypatch.setattr(rs, "DUAL_WRITE_ENABLED", True)
    monkeypatch.setattr(router, "_ENGINE", engine)
    monkeypatch.setattr(router, "COMPARE_MODE", False)
    monkeypatch.setattr(router, "READ_FAILSAFE_LEGACY", False)
    monkeypatch.setattr(
        "core.identity.default_organization.DEFAULT_ORGANIZATION_SLUG", ORG_SLUG
    )
    for flag in ALL_READ_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    yield engine
    engine.dispose()


def _enable(monkeypatch, entity: str) -> None:
    monkeypatch.setenv(router.READ_FLAGS[entity], "true")


# ---------------------------------------------------------------------------
# Core routing behavior — flag off/on, per entity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entity,create_kwargs",
    [
        ("leads", {"name": "A Lead", "source": "Website"}),
        ("corporate_clients", {"company_name": "Acme Corp"}),
        ("therapists", {"name": "Dr Smith", "weekly_capacity": 10}),
        ("patients", {"name": "Jane Doe", "email": "jane@example.com"}),
        ("package_templates", {"name": "10-pack", "total_sessions": 10, "price": 99.5}),
    ],
)
def test_flag_off_returns_legacy(isolated, entity, create_kwargs) -> None:
    row = crm.add_record(entity, create_kwargs)
    result = crm.list_records(entity)
    assert result == [row]


@pytest.mark.parametrize(
    "entity,create_kwargs",
    [
        ("leads", {"name": "A Lead", "source": "Website"}),
        ("corporate_clients", {"company_name": "Acme Corp"}),
        ("therapists", {"name": "Dr Smith", "weekly_capacity": 10}),
        ("patients", {"name": "Jane Doe", "email": "jane@example.com"}),
        ("package_templates", {"name": "10-pack", "total_sessions": 10, "price": 99.5}),
    ],
)
def test_flag_on_returns_equivalent_normalized_relational(isolated, monkeypatch, entity, create_kwargs) -> None:
    row = crm.add_record(entity, create_kwargs)
    _enable(monkeypatch, entity)
    result = crm.list_records(entity)
    assert len(result) == 1
    id_field = {
        "leads": "lead_id", "corporate_clients": "client_id", "therapists": "therapist_id",
        "patients": "patient_id", "package_templates": "template_id",
    }[entity]
    assert result[0][id_field] == row[id_field]
    for key, value in create_kwargs.items():
        if isinstance(value, float):
            assert Decimal(str(result[0][key])) == Decimal(str(value))
        else:
            assert result[0][key] == value


def test_empty_state(isolated, monkeypatch) -> None:
    assert crm.list_records("leads") == []
    _enable(monkeypatch, "leads")
    assert crm.list_records("leads") == []


def test_get_record_via_relational(isolated, monkeypatch) -> None:
    row = crm.add_record("patients", {"name": "Jane Doe"})
    _enable(monkeypatch, "patients")
    fetched = crm.get_record("patients", row["patient_id"])
    assert fetched is not None
    assert fetched["patient_id"] == row["patient_id"]
    assert fetched["name"] == "Jane Doe"


def test_get_record_missing_returns_none(isolated, monkeypatch) -> None:
    _enable(monkeypatch, "patients")
    assert crm.get_record("patients", "P-999") is None


def test_entities_are_independently_switchable(isolated, monkeypatch) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-04-01", "status": "Scheduled"})
    _enable(monkeypatch, "patients")
    # appointments flag NOT enabled — must still be legacy-sourced
    appts = crm.list_records("appointments")
    assert len(appts) == 1
    assert "created_at" in appts[0]  # legacy shape includes it as originally set


# ---------------------------------------------------------------------------
# Multiple records + ordering
# ---------------------------------------------------------------------------

def test_multiple_records_and_ordering_matches_legacy_insertion_order(isolated, monkeypatch) -> None:
    names = ["First", "Second", "Third", "Fourth"]
    for name in names:
        crm.add_record("leads", {"name": name, "source": "Website"})
    legacy_order = [r["name"] for r in crm.list_records("leads")]

    _enable(monkeypatch, "leads")
    relational_order = [r["name"] for r in crm.list_records("leads")]

    assert legacy_order == names
    assert relational_order == names


# ---------------------------------------------------------------------------
# Compare mode
# ---------------------------------------------------------------------------

def test_compare_mode_detects_equality_no_mismatch_recorded(isolated, monkeypatch) -> None:
    crm.add_record("leads", {"name": "Consistent Lead", "source": "Website"})
    monkeypatch.setattr(router, "COMPARE_MODE", True)
    crm.list_records("leads")  # triggers compare in the background

    with Session(isolated) as session:
        count = session.query(ReadMismatch).count()
    assert count == 0


def test_compare_mode_detects_mismatch(isolated, monkeypatch) -> None:
    row = crm.add_record("leads", {"name": "Will Drift", "source": "Website", "status": "New"})
    # Directly corrupt the relational copy so it disagrees with legacy.
    from core.db.models.clinic import Lead
    with Session(isolated) as session:
        lead = session.query(Lead).filter(Lead.external_id == row["lead_id"]).one()
        lead.status = __import__("core.db.models.clinic", fromlist=["LeadStatus"]).LeadStatus.CONTACTED
        session.commit()

    monkeypatch.setattr(router, "COMPARE_MODE", True)
    result = crm.list_records("leads")
    assert result[0]["status"] == "New"  # legacy result still returned (flag is off)

    with Session(isolated) as session:
        mismatches = session.query(ReadMismatch).all()
    assert len(mismatches) >= 1
    assert any(m.mismatch_category == "field_mismatch:status" for m in mismatches)


def test_compare_mode_never_changes_returned_result(isolated, monkeypatch) -> None:
    crm.add_record("leads", {"name": "Unaffected", "source": "Website"})
    without_compare = crm.list_records("leads")
    monkeypatch.setattr(router, "COMPARE_MODE", True)
    with_compare = crm.list_records("leads")
    assert without_compare == with_compare


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_relational_unavailable_raises_by_default_when_flag_on(isolated, monkeypatch) -> None:
    crm.add_record("leads", {"name": "A Lead", "source": "Website"})
    broken_engine = make_engine("sqlite:///:memory:")  # no create_all -> tables missing
    monkeypatch.setattr(router, "_ENGINE", broken_engine)
    _enable(monkeypatch, "leads")
    with pytest.raises(Exception):
        crm.list_records("leads")
    broken_engine.dispose()


def test_relational_unavailable_falls_back_when_failsafe_enabled(isolated, monkeypatch) -> None:
    row = crm.add_record("leads", {"name": "A Lead", "source": "Website"})
    broken_engine = make_engine("sqlite:///:memory:")
    monkeypatch.setattr(router, "_ENGINE", broken_engine)
    monkeypatch.setattr(router, "READ_FAILSAFE_LEGACY", True)
    _enable(monkeypatch, "leads")
    result = crm.list_records("leads")
    assert result == [row]
    broken_engine.dispose()


def test_compare_mode_relational_failure_does_not_break_legacy_read(isolated, monkeypatch) -> None:
    row = crm.add_record("leads", {"name": "A Lead", "source": "Website"})
    broken_engine = make_engine("sqlite:///:memory:")
    monkeypatch.setattr(router, "_ENGINE", broken_engine)
    monkeypatch.setattr(router, "COMPARE_MODE", True)
    result = crm.list_records("leads")  # flag still off; compare attempt fails silently (logged)
    assert result == [row]
    broken_engine.dispose()


# ---------------------------------------------------------------------------
# Patients — higher risk: archive/inactive semantics, relationships
# ---------------------------------------------------------------------------

def test_patient_archived_status_preserved_and_filtered(isolated, monkeypatch) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    crm.archive_record("patients", patient["patient_id"])
    _enable(monkeypatch, "patients")

    active_only = crm.list_records("patients")
    assert active_only == []
    everything = crm.list_records("patients", include_archived=True)
    assert len(everything) == 1
    assert everything[0]["status"] == "Archived"


def test_patient_relationship_fields_via_relational_appointments(isolated, monkeypatch) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    therapist = crm.add_record("therapists", {"name": "Dr Rao"})
    crm.add_record(
        "appointments",
        {"patient_id": patient["patient_id"], "therapist_id": therapist["therapist_id"], "appointment_date": "2026-04-01", "status": "Scheduled"},
    )
    _enable(monkeypatch, "appointments")
    appts = crm.list_records("appointments")
    assert appts[0]["patient_id"] == patient["patient_id"]
    assert appts[0]["therapist_id"] == therapist["therapist_id"]


# ---------------------------------------------------------------------------
# Appointments — high risk: date/status/therapist/patient filters
# ---------------------------------------------------------------------------

def test_appointment_filters_after_cutover(isolated, monkeypatch) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    therapist = crm.add_record("therapists", {"name": "Dr Rao"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "therapist_id": therapist["therapist_id"], "appointment_date": "2026-04-01", "status": "Scheduled"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-04-02", "status": "Completed"})
    _enable(monkeypatch, "appointments")

    rows = crm.list_records("appointments")
    scheduled = [r for r in rows if r["status"] == "Scheduled"]
    completed = [r for r in rows if r["status"] == "Completed"]
    by_date = [r for r in rows if r["appointment_date"] == "2026-04-01"]
    by_therapist = [r for r in rows if r["therapist_id"] == therapist["therapist_id"]]
    by_patient = [r for r in rows if r["patient_id"] == patient["patient_id"]]

    assert len(scheduled) == 1 and len(completed) == 1
    assert len(by_date) == 1
    assert len(by_therapist) == 1
    assert len(by_patient) == 2


def test_appointment_chronological_ordering_via_patient_profile(isolated, monkeypatch) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-01-01", "status": "Completed"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-03-01", "status": "Completed"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-02-01", "status": "Completed"})
    _enable(monkeypatch, "appointments")
    _enable(monkeypatch, "patients")

    profile = crm.patient_profile(patient["patient_id"])
    dates = [a["appointment_date"] for a in profile["appointments"]]
    assert dates == sorted(dates, reverse=True)  # patient_profile sorts newest-first itself


# ---------------------------------------------------------------------------
# Payments — must be last cutover; exact financial values
# ---------------------------------------------------------------------------

def test_payment_exact_financial_values_after_cutover(isolated, monkeypatch) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    crm.add_record("payments", {"patient_id": patient["patient_id"], "amount": 19.1, "payment_date": "2026-03-01", "status": "Paid", "method": "Card"})
    _enable(monkeypatch, "payments")

    rows = crm.list_records("payments")
    assert Decimal(str(rows[0]["amount"])) == Decimal("19.1")
    assert rows[0]["status"] == "Paid"
    assert rows[0]["method"] == "Card"


def test_payment_filters_and_ordering(isolated, monkeypatch) -> None:
    patient_a = crm.add_record("patients", {"name": "A"})
    patient_b = crm.add_record("patients", {"name": "B"})
    crm.add_record("payments", {"patient_id": patient_a["patient_id"], "amount": 100, "payment_date": "2026-01-01", "status": "Paid"})
    crm.add_record("payments", {"patient_id": patient_b["patient_id"], "amount": 200, "payment_date": "2026-01-02", "status": "Pending"})
    crm.add_record("payments", {"patient_id": patient_a["patient_id"], "amount": 300, "payment_date": "2026-01-03", "status": "Paid"})
    _enable(monkeypatch, "payments")

    rows = crm.list_records("payments")
    assert [r["amount"] for r in rows] == [100, 200, 300]  # insertion order preserved
    a_payments = [r for r in rows if r["patient_id"] == patient_a["patient_id"]]
    paid = [r for r in rows if r["status"] == "Paid"]
    assert len(a_payments) == 2
    assert len(paid) == 2


# ---------------------------------------------------------------------------
# Progress notes / treatments
# ---------------------------------------------------------------------------

def test_progress_note_relationships_after_cutover(isolated, monkeypatch) -> None:
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    therapist = crm.add_record("therapists", {"name": "Dr Rao"})
    crm.add_record(
        "progress_notes",
        {"patient_id": patient["patient_id"], "therapist_id": therapist["therapist_id"], "visit_date": "2026-03-01", "progress_summary": "Better."},
    )
    _enable(monkeypatch, "progress_notes")
    rows = crm.list_records("progress_notes")
    assert rows[0]["patient_id"] == patient["patient_id"]
    assert rows[0]["therapist_id"] == therapist["therapist_id"]
    assert rows[0]["progress_summary"] == "Better."


# ---------------------------------------------------------------------------
# Organization isolation (tenant adversarial)
# ---------------------------------------------------------------------------

def test_org_a_relational_read_cannot_return_org_b_patients(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A", slug="org-a")
        org_b = organization_service.create_organization(session, name="Org B", slug="org-b")
        rs.sync_one(session, org_a.id, "patients", {"patient_id": "P-A", "name": "Org A Patient", "status": "Active"})
        rs.sync_one(session, org_b.id, "patients", {"patient_id": "P-B", "name": "Org B Patient", "status": "Active"})
        session.commit()

        rows_a = router._read_relational_rows(session, org_a.id, "patients")
        rows_b = router._read_relational_rows(session, org_b.id, "patients")
        assert {r["patient_id"] for r in rows_a} == {"P-A"}
        assert {r["patient_id"] for r in rows_b} == {"P-B"}


def test_org_a_appointments_cannot_return_org_b_appointments(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A2", slug="org-a2")
        org_b = organization_service.create_organization(session, name="Org B2", slug="org-b2")
        rs.sync_one(session, org_a.id, "patients", {"patient_id": "P-A", "name": "A", "status": "Active"})
        rs.sync_one(session, org_b.id, "patients", {"patient_id": "P-B", "name": "B", "status": "Active"})
        rs.sync_one(session, org_a.id, "appointments", {"appointment_id": "A-A", "patient_id": "P-A", "appointment_date": "2026-01-01", "status": "Scheduled"})
        rs.sync_one(session, org_b.id, "appointments", {"appointment_id": "A-B", "patient_id": "P-B", "appointment_date": "2026-01-01", "status": "Scheduled"})
        session.commit()

        rows_a = router._read_relational_rows(session, org_a.id, "appointments")
        assert {r["appointment_id"] for r in rows_a} == {"A-A"}


def test_org_a_payments_cannot_return_org_b_payments(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A3", slug="org-a3")
        org_b = organization_service.create_organization(session, name="Org B3", slug="org-b3")
        rs.sync_one(session, org_a.id, "patients", {"patient_id": "P-A", "name": "A", "status": "Active"})
        rs.sync_one(session, org_b.id, "patients", {"patient_id": "P-B", "name": "B", "status": "Active"})
        rs.sync_one(session, org_a.id, "payments", {"payment_id": "PAY-A", "patient_id": "P-A", "amount": 50, "payment_date": "2026-01-01"})
        rs.sync_one(session, org_b.id, "payments", {"payment_id": "PAY-B", "patient_id": "P-B", "amount": 75, "payment_date": "2026-01-01"})
        session.commit()

        rows_a = router._read_relational_rows(session, org_a.id, "payments")
        assert {r["payment_id"] for r in rows_a} == {"PAY-A"}


def test_same_external_id_isolated_across_organizations(isolated) -> None:
    with Session(isolated) as session:
        org_a = organization_service.create_organization(session, name="Org A4", slug="org-a4")
        org_b = organization_service.create_organization(session, name="Org B4", slug="org-b4")
        rs.sync_one(session, org_a.id, "leads", {"lead_id": "L-SAME", "name": "For A", "source": "Website", "status": "New"})
        rs.sync_one(session, org_b.id, "leads", {"lead_id": "L-SAME", "name": "For B", "source": "Website", "status": "New"})
        session.commit()

        rows_a = router._read_relational_rows(session, org_a.id, "leads")
        rows_b = router._read_relational_rows(session, org_b.id, "leads")
        assert rows_a[0]["name"] == "For A"
        assert rows_b[0]["name"] == "For B"


def test_read_router_organization_id_is_keyword_only_and_defaults_to_trusted_resolution() -> None:
    """Phase 8 revision of this invariant: read_rows() now accepts an
    explicit organization_id — a deliberate, reviewed capability for
    trusted programmatic callers (scheduler per-organization enumeration,
    tests, provisioning verification) that already know exactly which
    already-authorized organization they're operating for. What must
    still hold, and is asserted here: it is keyword-only (never
    positionally slippable), defaults to None (omitting it always falls
    back to resolve_live_organization_id()'s trusted, session-based
    resolution — never an unset/zero organization), and the live,
    human-facing UI call sites never pass user-controlled input into it
    (verified separately in tests/test_phase8_saas_onboarding.py's
    "no untrusted organization_id" sweep)."""
    import inspect

    sig = inspect.signature(router.read_rows)
    assert "organization_id" in sig.parameters
    param = sig.parameters["organization_id"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


# ---------------------------------------------------------------------------
# Jarvis / scheduler compatibility — same read functions, different source
# ---------------------------------------------------------------------------

def test_jarvis_context_unaffected_by_read_cutover(isolated, monkeypatch) -> None:
    from services.jarvis_context import build_jarvis_context

    patient = crm.add_record("patients", {"name": "Jane Doe"})
    crm.add_record("appointments", {"patient_id": patient["patient_id"], "appointment_date": "2026-04-01", "status": "Scheduled"})

    before = build_jarvis_context()
    _enable(monkeypatch, "patients")
    _enable(monkeypatch, "appointments")
    after = build_jarvis_context()

    before_counts = {s["source"]: s["records"] for s in before["model_context"]["source_register"]}
    after_counts = {s["source"]: s["records"] for s in after["model_context"]["source_register"]}
    assert before_counts.get("clinic.patients") == after_counts.get("clinic.patients")
    assert before_counts.get("clinic.appointments") == after_counts.get("clinic.appointments")


def test_scheduler_reads_unaffected_by_cutover(isolated, monkeypatch) -> None:
    therapist = crm.add_record("therapists", {"name": "Dr Rao", "weekly_capacity": 10})
    patient = crm.add_record("patients", {"name": "Jane Doe"})
    crm.add_record(
        "appointments",
        {"patient_id": patient["patient_id"], "therapist_id": therapist["therapist_id"], "appointment_date": "2026-04-01", "status": "Scheduled"},
    )
    before = crm.list_records("appointments")
    _enable(monkeypatch, "appointments")
    _enable(monkeypatch, "therapists")
    _enable(monkeypatch, "patients")
    after = crm.list_records("appointments")
    assert len(before) == len(after) == 1
    assert before[0]["appointment_id"] == after[0]["appointment_id"]
