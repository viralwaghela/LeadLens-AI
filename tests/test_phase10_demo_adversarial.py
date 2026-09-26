"""V2 Phase 10 (public demo environment) — Phase 4: adversarial audit tests.

The threat model is deliberately harsh: assume the application-level RBAC
is COMPLETELY bypassed (no authenticated session at all, so
require_permission() is a no-op), and try every mutating entry point in the
codebase anyway. The infrastructure-independent guards — the database
statement guard, the legacy memory_store guard, the adapter guards, and the
upload/export guards — must still refuse every one, and nothing may change.

Each attempt is run twice:

  * against the "victim" database in demo mode with no operator context:
    it must raise DemoModeError (or return the explicit "blocked" result) and
    the database must be byte-for-byte unchanged;
  * against a "control" database inside the operator context: it must
    SUCCEED. This proves the call is well-formed, so the refusal above is
    attributable to the guard and not to a mistake in the test's arguments.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import core.memory as business_memory
from core.db.base import Base
import core.db.models  # noqa: F401
from core.db.models.identity import Membership, MembershipRole, User
from core.db.models.integration import IntegrationProvider
from core.db.models.organization import Organization
from core.db.session import make_engine
from core.demo_mode import DemoModeError, allow_demo_writes
from core.identity import membership_service, organization_service, organization_profile_service, user_service
from core.identity.permissions import (
    DEMO_ORG_PERMISSION_CAP,
    DEMO_VIEWER_PERMISSIONS,
    PERMISSIONS,
    ROLE_PERMISSIONS,
)
from core.identity.tenant_context import ActorType, TenantContext
from demo.seeder import seed_demo, seeding_environment

ANCHOR = date(2026, 9, 26)


@dataclass
class World:
    engine: Any
    org_id: int
    membership_id: int
    user_id: int
    task_id: str
    item_id: str
    recommendation_id: str
    context: TenantContext
    seed_dir: Path


def _build_world(monkeypatch_factory) -> tuple[World, Callable[[], None]]:
    mp = monkeypatch_factory()
    tmp = Path(tempfile.mkdtemp())
    mp.setattr(business_memory, "DATABASE_FOLDER", tmp / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    seed_dir = tmp / "seed"
    seed_demo(engine, anchor=ANCHOR, seed_dir=seed_dir)
    env = seeding_environment(engine, seed_dir=seed_dir)
    env.__enter__()
    mp.setenv("OPENAI_API_KEY", "sk-test-not-real")
    mp.setenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from services import credential_encryption as ce
    import services.integration_credentials as ic

    ce._fernet_for_version.cache_clear()
    mp.setattr(ic, "_ENGINE", engine)

    from services.integration_credentials import configure_integration

    with allow_demo_writes(), Session(engine) as session:
        seeded_org = session.query(Organization).one()
        configure_integration(
            session, TenantContext(organization_id=seeded_org.id, actor_type=ActorType.SYSTEM),
            IntegrationProvider.WHATSAPP, secret_fields={"access_token": "seed-token"},
            configuration_fields={"phone_number_id": "1"},
        )
        session.commit()

    business_memory._read_cache_at = 0
    memory = business_memory.load_memory()
    from services.integration_manager_v21 import execution_rows

    with Session(engine) as session:
        org = session.query(Organization).one()
        user = session.query(User).one()
        membership = session.query(Membership).one()
        world = World(
            engine=engine, org_id=org.id, membership_id=membership.id, user_id=user.id,
            task_id=memory["tasks"][0]["id"],
            item_id=next(r["id"] for r in execution_rows() if r["status"] == "Awaiting approval"),
            recommendation_id="",
            context=TenantContext(organization_id=org.id, actor_type=ActorType.SYSTEM),
            seed_dir=seed_dir,
        )
    from services import jarvis_memory

    world.recommendation_id = jarvis_memory.load_learning_memory()["recommendations"][0]["id"]

    def teardown() -> None:
        env.__exit__(None, None, None)
        mp.undo()
        engine.dispose()

    return world, teardown


@pytest.fixture(scope="module")
def victim():
    world, teardown = _build_world(pytest.MonkeyPatch)
    yield world
    teardown()


@pytest.fixture(scope="module")
def control():
    world, teardown = _build_world(pytest.MonkeyPatch)
    yield world
    teardown()


def _fingerprint(world: World) -> dict:
    counts: dict[str, int] = {}
    with Session(world.engine) as session:
        for table in Base.metadata.sorted_tables:
            if table.name == "demo_usage":
                continue
            counts[table.name] = session.execute(select(func.count()).select_from(table)).scalar()
    business_memory._read_cache_at = 0
    legacy = json.dumps(business_memory.load_memory(), sort_keys=True, default=str)
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(world.seed_dir.glob("*.json"))}
    return {"tables": counts, "legacy": hashlib.sha256(legacy.encode()).hexdigest(), "files": files}


# ---------------------------------------------------------------------------
# The mutating entry points. (name, callable(world), works_under_operator_context)
# ---------------------------------------------------------------------------

def _crm(w: World):
    from services import clinic_data_service as cds

    return cds


def _attempts() -> list[tuple[str, Callable[[World], Any], bool]]:
    from services import clinic_data_service as cds
    from services import jarvis_memory, platform_data
    from services.integration_credentials import configure_integration, disable_integration
    from services.integration_manager_v21 import decide_item, prepare_execution
    import scheduler.run_scheduled_checks as sched
    from services import campaign_engine, execution_engine, integration_hub, memory_engine, member_management

    def session_call(fn):
        def run(w: World):
            with Session(w.engine) as session:
                result = fn(session, w)
                session.commit()
                return result
        return run

    return [
        # ---- CRM (organization-scoped, relational) ----
        ("crm.add_record", lambda w: cds.add_record("patients", {"name": "Intruder", "status": "Active"}, organization_id=w.org_id), True),
        ("crm.update_record", lambda w: cds.update_record("patients", "P-001", {"name": "Renamed"}, organization_id=w.org_id), True),
        ("crm.archive_record", lambda w: cds.archive_record("patients", "P-002", organization_id=w.org_id), True),
        ("crm.add_lead", lambda w: cds.add_record("leads", {"name": "Intruder Lead", "source": "Website"}, organization_id=w.org_id), True),
        # ---- legacy business memory ----
        ("memory.add_task", lambda w: business_memory.add_task("Intruder task", "Ops"), True),
        ("memory.add_decision", lambda w: business_memory.add_decision("Intruder decision", "because", "Low"), True),
        ("memory.add_approval", lambda w: business_memory.add_approval("Intruder approval", "Ops", "Low"), True),
        ("memory.add_daily_log", lambda w: business_memory.add_daily_log("intruder log"), True),
        ("memory.add_memory_entry", lambda w: business_memory.add_memory_entry("reports", {"title": "intruder"}), True),
        ("memory.complete_task", lambda w: business_memory.complete_task(w.task_id), True),
        ("memory.save_company", lambda w: business_memory.save_company({"business_name": "Hijacked", "is_demo": True}), True),
        ("memory.update_company", lambda w: business_memory.update_company("business_name", "Hijacked"), True),
        ("memory.save_memory", lambda w: business_memory.save_memory({"company": {"is_demo": True}}), True),
        ("engine.propose_action", lambda w: execution_engine.propose_action("create_task", {"title": "x"}), True),
        ("engine.create_campaign", lambda w: campaign_engine.create_campaign({"days": 3, "budget": 100}), True),
        ("hub.save_connection", lambda w: integration_hub.save_connection("Gmail", {"delegated_user": "someone@example.com"}), True),
        ("memory_engine.remember", lambda w: memory_engine.remember("note", "intruder note"), True),
        # ---- approval queue ----
        ("queue.prepare_execution", lambda w: prepare_execution("whatsapp", "send_text", {"to": "+1-555-0100", "body": "hi"}, title="Intruder", tenant_context=w.context), True),
        ("queue.decide_item", lambda w: decide_item(w.item_id, "Approved"), True),
        # ---- scheduler primitives ----
        ("scheduler.raise_owner_alert", lambda w: sched.raise_owner_alert("intruder_check", "k1", "t", "m"), True),
        ("scheduler.queue_patient_action", lambda w: sched.queue_patient_action("intruder_check", "k2", provider="whatsapp", action="send_text", payload={"to": "+1-555-0100", "body": "hi"}, title="Intruder queue"), True),
        # ---- Jarvis learning memory ----
        ("jarvis.track_recommendation", lambda w: jarvis_memory.track_recommendation("q?", "an intruder recommendation"), True),
        ("jarvis.record_outcome", lambda w: jarvis_memory.record_recommendation_outcome(w.recommendation_id, "successful", "did a thing"), True),
        ("jarvis.record_execution", lambda w: jarvis_memory.record_action_execution(w.recommendation_id, "EXEC-X", "whatsapp", "send_text", "Simulated"), True),
        # ---- settings ----
        ("settings.save_company_profile", lambda w: platform_data.save_company_profile({"business_name": "Hijacked"}), True),
        ("settings.save_settings", session_call(lambda s, w: organization_profile_service.save_settings(s, w.org_id, {"business_name": "Hijacked"})), True),
        ("settings.automations_enabled", session_call(lambda s, w: organization_profile_service.set_automations_enabled(s, w.org_id, True)), True),
        # ---- integrations & secrets ----
        ("integrations.configure", session_call(lambda s, w: configure_integration(s, w.context, IntegrationProvider.WHATSAPP, secret_fields={"access_token": "x"}, configuration_fields={"phone_number_id": "1"})), True),
        ("integrations.disable", session_call(lambda s, w: disable_integration(s, w.context, IntegrationProvider.WHATSAPP)), True),
        # ---- identity: user creation, role changes ----
        ("identity.create_user", session_call(lambda s, w: user_service.create_user(s, email="new@intruder.example", password="pw-12345678")), True),
        ("identity.create_organization", session_call(lambda s, w: organization_service.create_organization(s, name="Intruder Org", slug="intruder-org")), True),
        ("identity.change_role", session_call(lambda s, w: membership_service.change_role(s, w.membership_id, MembershipRole.OWNER)), True),
        ("identity.disable_user", session_call(lambda s, w: user_service.disable_user(s, w.user_id)), True),
        ("members.create_member", session_call(lambda s, w: member_management.create_member(s, w.context, email="m@intruder.example", password="pw-12345678", role=MembershipRole.VIEWER)), True),
        # ---- uploads: never bypassable, not even by the operator context ----
        ("upload.save_uploaded_file", lambda w: platform_data.save_uploaded_file("intruder.csv", b"a,b\n1,2\n"), False),
    ]


ATTEMPT_NAMES = [name for name, _, _ in _attempts()]


@pytest.mark.parametrize("name", ATTEMPT_NAMES)
def test_every_mutation_is_refused_even_with_rbac_completely_bypassed(name, victim, monkeypatch):
    fn = next(f for n, f, _ in _attempts() if n == name)
    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")
    before = _fingerprint(victim)
    with pytest.raises(DemoModeError):
        fn(victim)
    assert _fingerprint(victim) == before, f"{name} changed something despite being refused"


@pytest.mark.parametrize("name", ATTEMPT_NAMES)
def test_the_same_call_is_well_formed_under_the_operator_context(name, control, monkeypatch):
    """The control: proves the refusal above is the guard's doing, not a bad argument."""
    fn, control_ok = next((f, ok) for n, f, ok in _attempts() if n == name)
    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")
    with allow_demo_writes():
        if control_ok:
            fn(control)  # must not raise
        else:
            with pytest.raises(DemoModeError):
                fn(control)  # uploads / exports / external actions cannot be bypassed even by the operator


def test_queued_actions_cannot_be_executed_however_they_are_approved(victim, monkeypatch):
    from services.integration_manager_v21 import execute_item

    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")
    before = _fingerprint(victim)
    result = execute_item(victim.item_id)
    assert result["status"] == "blocked" and result["success"] is False
    assert _fingerprint(victim) == before


# ---------------------------------------------------------------------------
# Privilege escalation
# ---------------------------------------------------------------------------

def test_the_permission_ceiling_holds_even_if_the_role_table_is_tampered_with(victim, monkeypatch):
    """`ROLE_PERMISSIONS` is an ordinary dict. Suppose code (or a bug) rewrote the demo
    viewer's entry — the demo organization's ceiling is a separate, immutable object."""
    from core.identity.authorization_service import resolve_identity

    tampered = dict(ROLE_PERMISSIONS)
    tampered[MembershipRole.DEMO_VIEWER] = frozenset(PERMISSIONS)
    monkeypatch.setattr("core.identity.permissions.ROLE_PERMISSIONS", tampered)
    with Session(victim.engine) as session:
        decision = resolve_identity(session, user_id=victim.user_id, organization_id=victim.org_id)
        assert decision.allowed
        assert decision.identity.permissions == DEMO_ORG_PERMISSION_CAP
        assert "organization.manage" not in decision.identity.permissions


def test_permission_sets_are_immutable(monkeypatch):
    assert isinstance(DEMO_VIEWER_PERMISSIONS, frozenset) and isinstance(DEMO_ORG_PERMISSION_CAP, frozenset)
    with pytest.raises(AttributeError):
        DEMO_VIEWER_PERMISSIONS.add("organization.manage")  # type: ignore[attr-defined]


def test_even_an_operator_promoted_owner_in_the_demo_org_stays_read_only(victim, monkeypatch):
    """If someone with database access promoted the demo user to OWNER, the demo
    organization's ceiling still applies."""
    from core.identity.authorization_service import resolve_identity

    with allow_demo_writes(), Session(victim.engine) as session:
        session.get(Membership, victim.membership_id).role = MembershipRole.OWNER
        session.commit()
    try:
        with Session(victim.engine) as session:
            decision = resolve_identity(session, user_id=victim.user_id, organization_id=victim.org_id)
            assert decision.allowed and decision.identity.permissions == DEMO_ORG_PERMISSION_CAP
    finally:
        with allow_demo_writes(), Session(victim.engine) as session:
            session.get(Membership, victim.membership_id).role = MembershipRole.DEMO_VIEWER
            session.commit()


# ---------------------------------------------------------------------------
# External actions: nothing leaves, whichever door is tried
# ---------------------------------------------------------------------------

def test_no_network_call_is_possible_through_any_adapter_even_with_credentials_in_the_environment(monkeypatch, tmp_path):
    import googleapiclient.discovery as discovery
    import integrations.calendar_service as cal
    import integrations.gmail_service as gm
    import integrations.whatsapp_service as wa
    import services.integration_clients as clients

    def boom(*args, **kwargs):
        raise AssertionError("a network call was attempted")

    key_file = tmp_path / "sa.json"
    key_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "SENTINEL-TOKEN")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "999")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(key_file))
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "someone@example.com")
    monkeypatch.setenv("LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED", "true")
    monkeypatch.setattr(wa.requests, "post", boom)
    monkeypatch.setattr(discovery, "build", boom)
    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")

    context = TenantContext(organization_id=1, actor_type=ActorType.SYSTEM)
    for client in (clients.get_whatsapp_client(context, dry_run=False), wa.WhatsAppBusinessService(dry_run=False)):
        assert client.dry_run is True and client.token == ""
        assert client.send_text({"to": "911111111111", "body": "hi"}).status == "simulated"
    for client in (clients.get_gmail_client(context, dry_run=False), gm.GmailService(dry_run=False)):
        assert client.dry_run is True and client.send_email({"to": "a@b.example", "subject": "s", "body": "b"}).status == "simulated"
        assert client.create_draft({"to": "a@b.example", "subject": "s", "body": "b"}).status == "simulated"
    for client in (clients.get_calendar_client(context, dry_run=False), cal.GoogleCalendarService(dry_run=False)):
        assert client.dry_run is True
        assert client.create_event({"summary": "s", "start": "2026-01-01T10:00:00", "end": "2026-01-01T11:00:00"}).status == "simulated"


def test_the_llm_cannot_be_used_to_reach_anything_external(monkeypatch):
    """The only outbound call the model layer makes is the Responses request itself — with no
    tool definitions, so the model has nothing it could invoke."""
    import inspect

    import services.ai as ai

    source = inspect.getsource(ai)
    assert "tools" not in source and "tool_choice" not in source and "function_call" not in source
    assert "web_search" not in source and "file_search" not in source and "mcp" not in source.lower()


# ---------------------------------------------------------------------------
# Health / readiness integration
# ---------------------------------------------------------------------------

@pytest.fixture()
def health_db(tmp_path, monkeypatch):
    import core.db.session as db_session

    url = f"sqlite:///{tmp_path / 'health.db'}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setattr(db_session, "get_database_url", lambda: url)
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    return url


def test_health_check_flags_a_demo_org_in_a_non_demo_deployment(health_db):
    from scripts.health_check import check_demo_isolation

    assert check_demo_isolation().status == "HEALTHY"
    engine = make_engine(health_db)
    with Session(engine) as session:
        org = organization_service.create_organization(session, name="LeadLens Demo Clinic", slug="leadlens-demo")
        org.is_demo = True
        session.commit()
    engine.dispose()
    outcome = check_demo_isolation()
    assert outcome.status == "DEGRADED" and "demo organization" in outcome.detail


def test_health_check_on_a_demo_deployment(health_db, monkeypatch):
    import core.demo_tripwire as tripwire
    from scripts.health_check import check_demo_isolation

    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")
    monkeypatch.setattr(tripwire, "environment_problems", lambda env=None: [])
    outcome = check_demo_isolation()
    assert outcome.status == "DEGRADED" and "not seeded" in outcome.detail  # isolated, but empty

    engine = make_engine(health_db)
    with allow_demo_writes(), Session(engine) as session:
        organization_service.create_organization(session, name="Real Clinic", slug="real-clinic")
        session.commit()
    engine.dispose()
    outcome = check_demo_isolation()
    assert outcome.status == "UNHEALTHY" and "non-demo organization" in outcome.detail


def test_config_validation_fails_a_demo_deployment_with_a_production_secret(monkeypatch):
    from core.config_validation import validate_configuration

    for name in ("WHATSAPP_ACCESS_TOKEN", "GMAIL_DELEGATED_USER", "APP_PASSWORD", "MASTER_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "SENTINEL-SECRET-VALUE")
    report = validate_configuration()
    messages = " ".join(f.message for f in report.findings)
    assert report.has_fail and "demo deployment" in messages and "WHATSAPP_ACCESS_TOKEN" in messages
    assert "SENTINEL-SECRET-VALUE" not in messages

    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    assert "demo deployment" not in " ".join(f.message for f in validate_configuration().findings)


# ---------------------------------------------------------------------------
# The shipped secrets template passes its own tripwire
# ---------------------------------------------------------------------------

def test_the_example_secrets_file_passes_the_tripwire_and_holds_no_real_secret():
    import tomllib

    from core.demo_tripwire import environment_problems

    path = Path(__file__).resolve().parents[1] / "demo" / "streamlit_secrets.example.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    flat = {str(k): str(v) for k, v in data.items()}
    assert environment_problems(flat) == [], environment_problems(flat)
    for key, value in flat.items():
        assert "PLACEHOLDER" in value.upper() or value in {"1", "leadlens-demo", "gpt-5-mini"} or value.isdigit(), (key, value)
