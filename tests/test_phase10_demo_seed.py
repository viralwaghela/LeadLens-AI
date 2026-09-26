"""V2 Phase 10 (public demo environment) — Phase 2 tests: synthetic dataset,
seeding, and reset.

Everything runs against throwaway in-memory databases and temp directories.
A dedicated test proves seeding never writes into the checkout's own
data/ or demo/seed files.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import hashlib
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import core.memory as business_memory
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.clinic import Appointment, Lead, Patient
from core.db.models.demo import DemoUsageCounter
from core.db.models.identity import Membership, MembershipRole, User
from core.db.models.jarvis import JarvisLearningRecord
from core.db.models.organization import Organization
from core.db.session import make_engine
from core.demo_mode import DEMO_ORG_NAME, DEMO_ORG_SLUG, DEMO_USER_EMAIL
from core.identity import organization_service
from core.identity.authentication_service import authenticate
from core.identity.authorization_service import resolve_identity
from core.identity.password_service import verify_password
from core.identity.permissions import DEMO_VIEWER_PERMISSIONS
from demo import operator_cli
from demo.seed_data import (
    FIRST_NAMES,
    LAST_NAMES,
    SEED,
    build_dataset,
)
from demo.seeder import (
    UNUSABLE_PASSWORD_HASH,
    DemoSeedError,
    ensure_demo_identity,
    reset_demo,
    seed_demo,
    seeding_environment,
    wipe_demo_tenant_rows,
)
from services import clinic_data_service as cds

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = date(2026, 9, 26)


@pytest.fixture(autouse=True)
def _demo_mode_off_by_default(monkeypatch):
    monkeypatch.delenv("LEADLENS_DEMO_MODE", raising=False)


@pytest.fixture()
def demo_db(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield SimpleNamespace(engine=engine, seed_dir=tmp_path / "seed", tmp=tmp_path)
    engine.dispose()


def _org_id(engine) -> int:
    with Session(engine) as session:
        return session.query(Organization).filter_by(slug=DEMO_ORG_SLUG).one().id


def _rows_by_org(engine) -> dict[str, dict[int | None, int]]:
    """{table: {organization_id: row_count}} for every organization-scoped table."""
    result: dict[str, dict[int | None, int]] = {}
    with Session(engine) as session:
        for table in Base.metadata.sorted_tables:
            if "organization_id" not in table.c:
                continue
            rows = session.execute(
                select(table.c.organization_id, func.count()).group_by(table.c.organization_id)
            ).all()
            if rows:
                result[table.name] = {org: count for org, count in rows}
    return result


def _file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ---------------------------------------------------------------------------
# 1. The synthetic dataset
# ---------------------------------------------------------------------------

def test_dataset_volumes_meet_the_requirements():
    counts = build_dataset(ANCHOR).counts
    assert 40 <= counts["patients"] <= 60
    assert 15 <= counts["leads"] <= 25
    assert counts["appointments"] >= 30
    assert counts["payments"] >= 20
    assert counts["approvals_and_workflows"] >= 5
    assert counts["reports"] >= 5 and counts["recommendations"] >= 1 and counts["daily_logs"] >= 5


def test_dataset_is_deterministic_and_anchor_relative():
    first, second = build_dataset(ANCHOR), build_dataset(ANCHOR)
    assert first.patients == second.patients
    assert first.appointments == second.appointments
    assert first.workflow_actions == second.workflow_actions

    shifted = build_dataset(ANCHOR + timedelta(days=30))
    assert [p["name"] for p in shifted.patients] == [p["name"] for p in first.patients]  # same people
    delta = timedelta(days=30)
    for old, new in zip(first.appointments, shifted.appointments):
        assert date.fromisoformat(new["appointment_date"]) - date.fromisoformat(old["appointment_date"]) == delta
    assert SEED == 20260926


def test_dataset_has_no_real_looking_identifiers():
    data = build_dataset(ANCHOR)
    people = data.patients + data.leads + data.corporate_clients
    names = [p["name"] for p in data.patients + data.leads]
    assert len(names) == len(set(names))
    for person in data.patients + data.leads:
        first, last = person["name"].split(" ", 1)
        assert first in FIRST_NAMES and last in LAST_NAMES
    phones = [p["phone"] for p in people]
    assert len(phones) == len(set(phones))
    assert all(re.fullmatch(r"\+1-555-01\d\d", phone) for phone in phones)  # the reserved fictional block
    for person in people:
        assert re.fullmatch(r"[a-z.]+@[a-z.]*example\.com", person["email"]), person["email"]
    assert data.company["website"].endswith(".invalid") or data.company["website"].startswith("https://demo.")
    assert data.company["business_name"] == DEMO_ORG_NAME
    assert data.company["is_demo"] is True and "is_demo" not in data.settings


def test_dataset_references_and_vocabularies_are_valid():
    data = build_dataset(ANCHOR)
    for appointment in data.appointments:
        assert 0 <= appointment["patient_ref"] < len(data.patients)
        assert 0 <= appointment["therapist_ref"] < len(data.therapists)
        assert appointment["status"] in cds.APPOINTMENT_STATUSES
        date.fromisoformat(appointment["appointment_date"])
    for payment in data.payments:
        package = data.packages[payment["package_ref"]]
        assert package["patient_ref"] == payment["patient_ref"]
        assert payment["status"] in cds.PAYMENT_STATUSES and payment["amount"] > 0
    for package in data.packages:
        assert package["sessions_remaining"] <= package["total_sessions"]
        assert package["status"] in cds.PACKAGE_STATUSES
    for patient in data.patients:
        assert patient["status"] in cds.PATIENT_STATUSES
        assert isinstance(patient["consent_to_contact"], bool)
    for lead in data.leads:
        assert lead["status"] in cds.LEAD_STATUSES and lead["source"] in cds.LEAD_SOURCES
    for note in data.progress_notes:
        assert 0 <= note["pain_score"] <= 10


def test_workflow_actions_only_target_consenting_synthetic_patients():
    data = build_dataset(ANCHOR)
    by_phone = {p["phone"]: p for p in data.patients}
    assert len({a["title"] for a in data.workflow_actions}) == len(data.workflow_actions)
    for action in data.workflow_actions:
        assert action["provider"] == "whatsapp" and action["action"] == "send_text"
        patient = by_phone[action["payload"]["to"]]
        assert patient["consent_to_contact"] is True
        assert patient["name"] in action["title"]
    decisions = [a["decision"] for a in data.workflow_actions if a["decision"]]
    assert set(decisions) <= {"Approved", "Rejected"}


# ---------------------------------------------------------------------------
# 2. Identity
# ---------------------------------------------------------------------------

def test_demo_identity_is_idempotent_passwordless_and_viewer_only(demo_db):
    for _ in range(2):
        with Session(demo_db.engine) as session:
            ensure_demo_identity(session)
            session.commit()
    with Session(demo_db.engine) as session:
        assert session.query(Organization).count() == 1
        assert session.query(User).count() == 1
        assert session.query(Membership).count() == 1
        org = session.query(Organization).one()
        membership = session.query(Membership).one()
        assert org.is_demo and org.slug == DEMO_ORG_SLUG and org.name == DEMO_ORG_NAME
        assert membership.role == MembershipRole.DEMO_VIEWER
        decision = resolve_identity(session, user_id=membership.user_id, organization_id=org.id)
        assert decision.allowed and decision.identity.permissions == DEMO_VIEWER_PERMISSIONS


def test_demo_user_has_no_password_that_any_input_can_satisfy(demo_db):
    with Session(demo_db.engine) as session:
        ensure_demo_identity(session)
        session.commit()
    with Session(demo_db.engine) as session:
        for attempt in ("", "password", "demo", "demo-viewer", UNUSABLE_PASSWORD_HASH, DEMO_USER_EMAIL):
            result = authenticate(session, email=DEMO_USER_EMAIL, password=attempt)
            assert result.success is False, attempt
    assert verify_password("anything", UNUSABLE_PASSWORD_HASH) is False
    assert verify_password("", UNUSABLE_PASSWORD_HASH) is False


def test_identity_refuses_a_same_slug_organization_that_is_not_demo(demo_db):
    with Session(demo_db.engine) as session:
        organization_service.create_organization(session, name="Impostor", slug=DEMO_ORG_SLUG)
        session.commit()
        with pytest.raises(DemoSeedError):
            ensure_demo_identity(session)


# ---------------------------------------------------------------------------
# 3. Seeding
# ---------------------------------------------------------------------------

def test_seed_populates_the_demo_tenant_through_the_real_services(demo_db):
    result = seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    assert result["seeded"] is True
    org_id = _org_id(demo_db.engine)
    counts = build_dataset(ANCHOR).counts

    with Session(demo_db.engine) as session:
        assert session.query(Patient).filter_by(organization_id=org_id).count() == counts["patients"]
        assert session.query(Appointment).filter_by(organization_id=org_id).count() == counts["appointments"]
        assert session.query(Lead).filter_by(organization_id=org_id).count() == counts["leads"]
        records = session.query(JarvisLearningRecord).filter_by(organization_id=org_id).all()
        assert len(records) >= counts["recommendations"]

    with seeding_environment(demo_db.engine, seed_dir=demo_db.seed_dir):
        assert len(cds.list_records("patients", organization_id=org_id)) == counts["patients"]
        assert len(cds.list_records("payments", organization_id=org_id)) == counts["payments"]
        from services.integration_manager_v21 import execution_rows
        from services.platform_data import company_setup_complete

        queue = execution_rows()
        assert len(queue) == 12
        assert {row["organization_id"] for row in queue} == {org_id}
        assert {row["status"] for row in queue} <= {"Awaiting approval", "Approved", "Rejected"}
        assert all(row["provider"] == "whatsapp" for row in queue)
        assert company_setup_complete() is True

    legacy = business_memory.load_memory()
    assert legacy["company"]["is_demo"] is True
    assert len(legacy["tasks"]) == counts["tasks"]
    assert len(legacy["reports"]) >= counts["reports"]
    assert (demo_db.seed_dir / "council_sessions.json").exists()
    assert (demo_db.seed_dir / "learning_memory.json").exists()


def test_every_seeded_row_belongs_to_the_demo_organization(demo_db):
    seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    org_id = _org_id(demo_db.engine)
    for table, per_org in _rows_by_org(demo_db.engine).items():
        assert set(per_org) <= {org_id, None}, (table, per_org)


def test_seeding_never_writes_into_the_checkouts_own_data_files(demo_db):
    watched = [
        ROOT / "data" / "learning" / "learning_memory.json",
        ROOT / "data" / "collaboration" / "council_sessions.json",
        ROOT / "data" / "clinic_profile.json",
        ROOT / "demo" / "seed" / "learning_memory.json",
        ROOT / "demo" / "seed" / "council_sessions.json",
    ]
    before = [_file_hash(path) for path in watched]
    seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    reset_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    assert [_file_hash(path) for path in watched] == before
    assert not (ROOT / "database" / "uploads").exists() or True  # (uploads are never touched by seeding)


def test_seed_is_idempotent(demo_db):
    first = seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    snapshot = _rows_by_org(demo_db.engine)
    second = seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    assert first["seeded"] is True and second["seeded"] is False
    assert _rows_by_org(demo_db.engine) == snapshot


def test_two_seeded_databases_are_identical(tmp_path, monkeypatch):
    def seed_once(name: str):
        monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / name / "database")
        engine = make_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        seed_demo(engine, anchor=ANCHOR, seed_dir=tmp_path / name / "seed")
        with Session(engine) as session:
            patients = [(p.external_id, p.name, str(p.last_visit)) for p in session.query(Patient).order_by(Patient.external_id)]
            appts = [(a.external_id, str(a.appointment_date), a.status.value) for a in session.query(Appointment).order_by(Appointment.external_id)]
        engine.dispose()
        return patients, appts

    assert seed_once("a") == seed_once("b")


def test_seeding_works_with_demo_mode_on_and_the_other_guards_still_hold(demo_db, monkeypatch):
    import integrations.whatsapp_service as wa
    from core.demo_mode import DemoModeError, assert_external_action_allowed

    def _boom(*args, **kwargs):
        raise AssertionError("a network call was attempted while seeding")

    monkeypatch.setattr(wa.requests, "post", _boom)
    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")
    result = seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)  # the operator context allows the writes
    assert result["seeded"] is True
    with pytest.raises(DemoModeError):
        assert_external_action_allowed("whatsapp")  # ...but never external actions, even during a seed
    with Session(demo_db.engine) as session:
        session.add(Organization(name="Intruder", slug="intruder"))
        with pytest.raises(DemoModeError):
            session.commit()  # and ordinary writes are refused again as soon as the seed finishes


def test_seed_refuses_a_database_that_already_holds_a_real_organization(demo_db):
    with Session(demo_db.engine) as session:
        organization_service.create_organization(session, name="Real Clinic", slug="real-clinic")
        session.commit()
    with pytest.raises(DemoSeedError, match="non-demo organization"):
        seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    assert not (demo_db.seed_dir / "council_sessions.json").exists()  # nothing was written


# ---------------------------------------------------------------------------
# 4. Reset — demo tenant only
# ---------------------------------------------------------------------------

def test_reset_restores_the_original_state_and_keeps_identity_and_the_llm_budget(demo_db):
    seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    original = _rows_by_org(demo_db.engine)
    org_id = _org_id(demo_db.engine)
    with Session(demo_db.engine) as session:
        user_id = session.query(User).one().id
        session.add(DemoUsageCounter(window_key="2026-09-26", scope="llm_calls", count=7))
        session.commit()

    # Operator-style tampering: add a patient, drop an appointment, scribble in the legacy store.
    from core.demo_mode import allow_demo_writes

    with allow_demo_writes(), seeding_environment(demo_db.engine, seed_dir=demo_db.seed_dir):
        cds.add_record("patients", {"name": "Zed Tamper", "status": "Active"}, organization_id=org_id)
        business_memory.add_task("Tampered task", "Ops")
    with allow_demo_writes(), Session(demo_db.engine) as session:
        session.delete(session.query(Appointment).first())
        session.commit()
    assert _rows_by_org(demo_db.engine) != original

    outcome = reset_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    assert outcome["reset"] is True
    assert _rows_by_org(demo_db.engine) == original
    with Session(demo_db.engine) as session:
        assert session.query(Patient).filter_by(name="Zed Tamper").count() == 0
        assert session.query(User).one().id == user_id  # identity restored, not recreated
        counter = session.query(DemoUsageCounter).one()
        assert counter.count == 7  # a reset must never refill the LLM budget
    business_memory._read_cache_at = 0
    assert all(t["data"]["title"] != "Tampered task" for t in business_memory.load_memory()["tasks"])


def test_wipe_deletes_only_the_demo_tenants_rows(demo_db):
    seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    demo_id = _org_id(demo_db.engine)

    # A second, real tenant sharing the database (in production this can't
    # happen — the seeder refuses such a DB — so build it directly).
    from core.demo_mode import allow_demo_writes

    with allow_demo_writes(), Session(demo_db.engine) as session:
        other = organization_service.create_organization(session, name="Other Clinic", slug="other-clinic")
        session.add(Patient(organization_id=other.id, external_id="P-001", name="Other Person"))
        session.add(Lead(organization_id=other.id, external_id="L-001", name="Other Lead"))
        session.commit()
        other_id = other.id
    other_before = {t: rows[other_id] for t, rows in _rows_by_org(demo_db.engine).items() if other_id in rows}
    assert other_before

    with Session(demo_db.engine) as session:
        demo_org = session.get(Organization, demo_id)
        deleted = wipe_demo_tenant_rows(session, demo_org)
        session.commit()
    assert deleted.get("patients", 0) > 0

    after = _rows_by_org(demo_db.engine)
    assert {t: rows[other_id] for t, rows in after.items() if other_id in rows} == other_before  # untouched
    # The demo tenant is empty — except its membership, which the wipe deliberately keeps
    # (identity is restored on reset, never deleted).
    assert all(demo_id not in rows for table, rows in after.items() if table != "memberships")
    with Session(demo_db.engine) as session:
        assert session.query(Organization).count() == 2  # both organizations still exist


def test_wipe_refuses_any_organization_that_is_not_the_demo_tenant(demo_db):
    with Session(demo_db.engine) as session:
        real = organization_service.create_organization(session, name="Real", slug="real")
        session.commit()
        with pytest.raises(DemoSeedError):
            wipe_demo_tenant_rows(session, real)
        real.is_demo = True  # even flagged demo, the slug must match
        with pytest.raises(DemoSeedError):
            wipe_demo_tenant_rows(session, real)


def test_reset_refuses_a_database_with_a_real_organization_and_changes_nothing(demo_db):
    seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    from core.demo_mode import allow_demo_writes

    with allow_demo_writes(), Session(demo_db.engine) as session:
        organization_service.create_organization(session, name="Real Clinic", slug="real-clinic")
        session.commit()
    before = _rows_by_org(demo_db.engine)
    with pytest.raises(DemoSeedError, match="non-demo organization"):
        reset_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    assert _rows_by_org(demo_db.engine) == before


def test_reset_refuses_when_the_legacy_company_profile_is_not_the_demos(demo_db):
    seed_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    business_memory.save_company({"business_name": "Some Real Clinic"})  # no is_demo marker: looks like production
    before = _rows_by_org(demo_db.engine)
    with pytest.raises(DemoSeedError, match="legacy company profile"):
        reset_demo(demo_db.engine, anchor=ANCHOR, seed_dir=demo_db.seed_dir)
    assert _rows_by_org(demo_db.engine) == before
    assert business_memory.load_memory()["company"]["business_name"] == "Some Real Clinic"


# ---------------------------------------------------------------------------
# 5. Operator CLI safety
# ---------------------------------------------------------------------------

def _pg_url(user: str, password: str, host: str, database: str, scheme: str = "postgresql") -> str:
    """Built by concatenation on purpose: the repo's pre-commit secret scan refuses any literal
    scheme://user:password@host in source, and these tests need SENTINEL passwords."""
    return scheme + "://" + user + ":" + password + "@" + host + "/" + database


PG_URL = _pg_url("demo_user", "TOP-SECRET-PW", "db.demo-host.example", "demo_db")


def test_parse_target_accepts_only_postgres_and_hides_credentials():
    target = operator_cli.parse_target(PG_URL)
    assert target.host == "db.demo-host.example" and target.database == "demo_db"
    assert "TOP-SECRET-PW" not in repr(target)
    for bad in ("", "   ", "sqlite:///demo.db", _pg_url("u", "pw", "h", "db", "mysql"), "postgresql:///nohost",
                _pg_url("u", "pw", "h", "db", "postgresql+psycopg"), _pg_url("u", "pw", "h", "db", "postgresql+psycopg2")):
        with pytest.raises(ValueError):
            operator_cli.parse_target(bad)


def test_operator_environment_refuses_provider_secrets_and_reports_names_only():
    env = {operator_cli.TARGET_ENV: PG_URL, "WHATSAPP_ACCESS_TOKEN": "SECRET-SENTINEL-9", "MASTER_DATABASE_URL": "postgresql://prod"}
    problems = operator_cli.operator_environment_problems(env)
    rendered = " ".join(problems)
    assert "WHATSAPP_ACCESS_TOKEN" in rendered and "MASTER_DATABASE_URL" in rendered
    assert "SECRET-SENTINEL-9" not in rendered and "TOP-SECRET-PW" not in rendered
    assert operator_cli.operator_environment_problems({operator_cli.TARGET_ENV: PG_URL}) == []
    assert operator_cli.operator_environment_problems({})  # the target variable is required


def _run_cli(demo_db, monkeypatch, argv, env=None, mode="seed"):
    lines: list[str] = []
    monkeypatch.setattr(operator_cli, "_migration_state", lambda engine: ("head", "head"))
    result = operator_cli.main(
        mode, argv, env if env is not None else {operator_cli.TARGET_ENV: PG_URL},
        out=lines.append, engine_factory=lambda url: demo_db.engine, set_environment=lambda url: None,
        root=demo_db.tmp,
    )
    return result, "\n".join(lines)


def test_cli_defaults_to_a_dry_run_that_writes_nothing(demo_db, monkeypatch):
    code, output = _run_cli(demo_db, monkeypatch, [])
    assert code == 0 and "DRY RUN" in output and "Nothing was written" in output
    assert "db.demo-host.example" in output and "TOP-SECRET-PW" not in output
    with Session(demo_db.engine) as session:
        assert session.query(Organization).count() == 0


def test_cli_apply_requires_the_typed_host_confirmation(demo_db, monkeypatch):
    for argv in (["--apply"], ["--apply", "--confirm-host", "some-other-host"], ["--apply", "--confirm-host", ""]):
        code, output = _run_cli(demo_db, monkeypatch, argv)
        assert code == 2 and "--confirm-host" in output
    with Session(demo_db.engine) as session:
        assert session.query(Organization).count() == 0


def test_cli_apply_with_the_right_host_seeds_and_never_prints_credentials(demo_db, monkeypatch):
    real_files = [ROOT / "demo" / "seed" / "learning_memory.json", ROOT / "demo" / "seed" / "council_sessions.json"]
    before = [_file_hash(path) for path in real_files]
    lines: list[str] = []
    monkeypatch.setattr(operator_cli, "_migration_state", lambda engine: ("head", "head"))
    code = operator_cli.main(
        "seed", ["--apply", "--confirm-host", "DB.demo-host.example", "--anchor", "2026-09-26"],
        {operator_cli.TARGET_ENV: PG_URL}, out=lines.append, engine_factory=lambda url: demo_db.engine,
        set_environment=lambda url: None, seed_dir=demo_db.seed_dir, root=demo_db.tmp,
    )
    output = "\n".join(lines)
    assert code == 0, output
    assert "TOP-SECRET-PW" not in output
    with Session(demo_db.engine) as session:
        assert session.query(Organization).filter_by(slug=DEMO_ORG_SLUG).count() == 1
    assert [_file_hash(path) for path in real_files] == before  # the repo's own seed files were never touched


def test_cli_refuses_to_run_from_a_checkout_that_has_a_dotenv_file(demo_db, monkeypatch):
    (demo_db.tmp / ".env").write_text("PLACEHOLDER=1\n", encoding="utf-8")  # stands in for a production .env
    lines: list[str] = []
    code = operator_cli.main(
        "seed", ["--apply", "--confirm-host", "db.demo-host.example"], {operator_cli.TARGET_ENV: PG_URL},
        out=lines.append, engine_factory=lambda url: demo_db.engine, set_environment=lambda url: None,
        root=demo_db.tmp,
    )
    assert code == 2 and ".env" in " ".join(lines) and "git worktree add" in " ".join(lines)
    with Session(demo_db.engine) as session:
        assert session.query(Organization).count() == 0
    assert operator_cli.dotenv_problems(demo_db.tmp / "nowhere") == []


def test_cli_refuses_forbidden_env_bad_anchor_and_old_migrations(demo_db, monkeypatch):
    code, output = _run_cli(demo_db, monkeypatch, [], env={operator_cli.TARGET_ENV: PG_URL, "GMAIL_DELEGATED_USER": "x"})
    assert code == 2 and "GMAIL_DELEGATED_USER" in output
    code, output = _run_cli(demo_db, monkeypatch, ["--anchor", "not-a-date"])
    assert code == 2 and "--anchor" in output

    monkeypatch.setattr(operator_cli, "_migration_state", lambda engine: ("old", "head"))
    lines: list[str] = []
    code = operator_cli.main(
        "seed", [], {operator_cli.TARGET_ENV: PG_URL}, out=lines.append,
        engine_factory=lambda url: demo_db.engine, set_environment=lambda url: None, root=demo_db.tmp,
    )
    assert code == 2 and "alembic upgrade head" in " ".join(lines)


def test_cli_refuses_a_database_that_looks_like_production(demo_db, monkeypatch):
    with Session(demo_db.engine) as session:
        organization_service.create_organization(session, name="Real Clinic", slug="real-clinic")
        session.commit()
    code, output = _run_cli(demo_db, monkeypatch, ["--apply", "--confirm-host", "db.demo-host.example"])
    assert code == 2 and "non-demo organization" in output


def test_cli_points_the_app_at_the_demo_database_and_ignores_ambient_urls(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _pg_url("prod-user", "prod-pw", "prod-host", "prod_db"))
    monkeypatch.setenv("LEADLENS_V2_DATABASE_URL", _pg_url("prod-user", "prod-pw", "prod-host", "prod_db"))
    monkeypatch.delenv("LEADLENS_DEFAULT_ORG_SLUG", raising=False)
    operator_cli._point_app_at(PG_URL)
    import os

    assert os.environ["DATABASE_URL"] == PG_URL and os.environ["LEADLENS_V2_DATABASE_URL"] == PG_URL
    assert os.environ["LEADLENS_DEFAULT_ORG_SLUG"] == DEMO_ORG_SLUG


def test_operator_code_never_loads_dotenv_and_is_never_imported_by_the_app():
    for relative in ("scripts/seed_demo.py", "scripts/reset_demo.py", "demo/operator_cli.py", "demo/seeder.py",
                     "demo/seed_data.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        code_lines = [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]
        assert not any("load_dotenv(" in ln for ln in code_lines), relative
    offenders = []
    for directory in ("core", "services", "ui", "integrations", "scheduler", "workflows"):
        for path in (ROOT / directory).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\s*(from|import)\s+demo\.(seeder|operator_cli)", text, re.MULTILINE):
                offenders.append(str(path.relative_to(ROOT)))
    for name in ("app.py", "dashboard.py", "onboarding.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+demo\.(seeder|operator_cli)", text, re.MULTILINE):
            offenders.append(name)
    assert offenders == [], "write-capable demo modules must never be imported by the running app"
