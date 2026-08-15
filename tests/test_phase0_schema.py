"""V2 Phase 0 schema tests.

These are schema-boundary tests only. They prove the relational schema
can represent multiple organizations with correct isolation at the
database level (foreign keys, unique constraints). They do NOT prove
live application tenant isolation — no live service in this repository
(clinic_data_service.py, jarvis_context.py, scheduler/, integration_
manager_v21.py) reads or writes any table defined in core/db/models/ yet.
See docs/V2_COEXISTENCE.md.

Every test here uses its own private, temporary SQLite database, created
fresh and discarded per test — never core/memory.py's database, never a
real DATABASE_URL. import _bootstrap first, same as every other file in
tests/, for defense in depth even though these tests don't touch
core.memory directly.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.clinic import Patient, PatientStatus
from core.db.models.identity import Membership, MembershipRole, MembershipStatus, User, UserStatus
from core.db.models.organization import Organization, OrganizationStatus
from core.db.session import make_engine

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def engine():
    # Uses the real make_engine() helper, not a bare create_engine() call,
    # so these tests exercise (and are protected by) the same SQLite
    # foreign-key-enforcement fix the application code itself gets —
    # see core/db/session.py's PRAGMA foreign_keys=ON connect listener.
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_all_tables_create_cleanly(engine) -> None:
    table_names = set(Base.metadata.tables.keys())
    expected = {
        "organizations", "organization_settings", "users", "memberships",
        "patients", "appointments", "package_templates", "packages",
        "payments", "progress_notes", "leads", "corporate_clients",
        "services", "therapists", "scheduler_runs", "scheduler_alert_ledger",
        "approvals", "execution_queue_items", "security_audit_events",
        "jarvis_learning_records",
    }
    assert expected <= table_names, f"missing tables: {expected - table_names}"


def test_alembic_upgrade_from_empty_database_reaches_head() -> None:
    """Runs the real `alembic upgrade head` CLI as a subprocess against a
    brand-new temp SQLite file, with an explicit env so it can never pick
    up a real DATABASE_URL from the environment or a .env file — even
    though tests/_bootstrap.py already guards this process, a subprocess
    does not inherit that monkeypatch, so the guard is redone here via an
    explicit, minimal environment instead."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "phase0_test.db"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "DATABASE_URL": f"sqlite:///{db_path}",
        }
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert db_path.exists(), "alembic did not create the target database file"

        check = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert check.returncode == 0, (
            f"alembic check found drift after a fresh upgrade:\n"
            f"stdout: {check.stdout}\nstderr: {check.stderr}"
        )
        assert "No new upgrade operations detected" in check.stdout


def test_composite_foreign_keys_enforce_same_organization(session) -> None:
    """The exact bug the composite FKs in core/db/models/clinic.py exist
    to prevent: an Appointment in one organization must not be able to
    reference a Patient belonging to a different organization."""
    org_a = Organization(name="Org A", slug="org-a")
    org_b = Organization(name="Org B", slug="org-b")
    session.add_all([org_a, org_b])
    session.flush()

    patient_in_b = Patient(organization_id=org_b.id, name="Cross-tenant Patient")
    session.add(patient_in_b)
    session.flush()

    from core.db.models.clinic import Appointment
    from datetime import date

    bad_appointment = Appointment(
        organization_id=org_a.id,  # org A ...
        patient_id=patient_in_b.id,  # ... referencing org B's patient
        appointment_date=date.today(),
    )
    session.add(bad_appointment)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

def test_create_organization(session) -> None:
    org = Organization(name="Beyond Pain", slug="beyond-pain")
    session.add(org)
    session.flush()
    assert org.id is not None
    assert org.status == OrganizationStatus.ACTIVE  # default


def test_duplicate_slug_rejected(session) -> None:
    session.add(Organization(name="Beyond Pain", slug="beyond-pain"))
    session.flush()
    session.add(Organization(name="Beyond Pain (duplicate)", slug="beyond-pain"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_inactive_status_supported(session) -> None:
    org = Organization(name="Paused Clinic", slug="paused-clinic", status=OrganizationStatus.INACTIVE)
    session.add(org)
    session.flush()
    assert org.status == OrganizationStatus.INACTIVE


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

def test_user_can_belong_to_organization(session) -> None:
    org = Organization(name="Beyond Pain", slug="beyond-pain")
    user = User(email="owner@example.com", password_hash="not-a-real-hash", status=UserStatus.ACTIVE)
    session.add_all([org, user])
    session.flush()

    membership = Membership(
        user_id=user.id,
        organization_id=org.id,
        role=MembershipRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    session.add(membership)
    session.flush()
    assert membership.id is not None


def test_duplicate_membership_rejected(session) -> None:
    org = Organization(name="Beyond Pain", slug="beyond-pain")
    user = User(email="owner@example.com", password_hash="not-a-real-hash")
    session.add_all([org, user])
    session.flush()

    session.add(Membership(user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER))
    session.flush()
    session.add(Membership(user_id=user.id, organization_id=org.id, role=MembershipRole.PRACTITIONER))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_one_user_can_have_memberships_in_multiple_organizations(session) -> None:
    org_a = Organization(name="Org A", slug="org-a")
    org_b = Organization(name="Org B", slug="org-b")
    user = User(email="consultant@example.com", password_hash="not-a-real-hash")
    session.add_all([org_a, org_b, user])
    session.flush()

    session.add(Membership(user_id=user.id, organization_id=org_a.id, role=MembershipRole.OWNER))
    session.add(Membership(user_id=user.id, organization_id=org_b.id, role=MembershipRole.VIEWER))
    session.flush()

    memberships = session.query(Membership).filter(Membership.user_id == user.id).all()
    assert len(memberships) == 2
    assert {m.organization_id for m in memberships} == {org_a.id, org_b.id}


# ---------------------------------------------------------------------------
# Tenant data foundation — the two-organization test the task asks for
# explicitly. Schema-boundary only: proves the DATABASE allows this, not
# that any live service enforces it yet (nothing live reads this table).
# ---------------------------------------------------------------------------

def test_two_organizations_can_have_same_named_patient_without_collision(session) -> None:
    org_a = Organization(name="Clinic A", slug="clinic-a")
    org_b = Organization(name="Clinic B", slug="clinic-b")
    session.add_all([org_a, org_b])
    session.flush()

    patient_a = Patient(
        organization_id=org_a.id,
        external_id="P-001",
        name="Test Patient",
        status=PatientStatus.ACTIVE,
    )
    patient_b = Patient(
        organization_id=org_b.id,
        external_id="P-001",  # same external_id string, different org: must be allowed
        name="Test Patient",  # same human name: must always be allowed, never unique
        status=PatientStatus.ACTIVE,
    )
    session.add_all([patient_a, patient_b])
    session.flush()  # must not raise — this is the actual assertion

    assert patient_a.id != patient_b.id
    assert patient_a.organization_id != patient_b.organization_id

    # And the reverse must still be true: the SAME org cannot have two
    # patients with the same external_id — organization-scoped
    # uniqueness must actually be enforced, not just "no accidental
    # global uniqueness."
    session.add(Patient(organization_id=org_a.id, external_id="P-001", name="Someone Else"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_patient_name_is_never_globally_or_org_uniquely_constrained(session) -> None:
    """Two different real people can coincidentally share a name within
    the SAME clinic — that must not be blocked by the schema."""
    org = Organization(name="Clinic A", slug="clinic-a")
    session.add(org)
    session.flush()

    session.add(Patient(organization_id=org.id, external_id="P-001", name="Same Name"))
    session.add(Patient(organization_id=org.id, external_id="P-002", name="Same Name"))
    session.flush()  # must not raise
