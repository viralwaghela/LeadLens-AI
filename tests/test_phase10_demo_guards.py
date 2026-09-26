"""V2 Phase 10 (public demo environment) — Phase 1 tests: schema, role, and
the server-side guards.

Every guard is tested in BOTH directions: refuses in demo mode, and is a
complete no-op when demo mode is off (so nothing here can change a
production/client deployment). The last section is a set of source-level
invariants: a future unguarded write / export / upload / outbound call fails
a test here until someone reviews it.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import core.memory as business_memory
from core import demo_llm
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db import demo_guard
from core.db.models.demo import DemoUsageCounter
from core.db.models.identity import MembershipRole
from core.db.models.organization import Organization, OrganizationSettings
from core.db.session import make_engine
from core.demo_mode import (
    DEMO_DISABLED_MESSAGE,
    DEMO_USAGE_CAP_MESSAGE,
    DemoModeError,
    DemoUsageCapReached,
    allow_demo_writes,
    assert_export_allowed,
    assert_external_action_allowed,
    assert_upload_allowed,
    assert_write_allowed,
    demo_mode_enabled,
)
from core.identity import membership_service, organization_service, user_service
from core.identity.authorization_service import authorize, resolve_identity
from core.identity.permissions import (
    DEMO_ORG_PERMISSION_CAP,
    DEMO_VIEWER_PERMISSIONS,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    cap_permissions_for_org,
)
from core.identity.tenant_context import build_user_context

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _demo_mode_off_by_default(monkeypatch):
    monkeypatch.delenv("LEADLENS_DEMO_MODE", raising=False)


@pytest.fixture()
def engine():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _demo_on(monkeypatch):
    monkeypatch.setenv("LEADLENS_DEMO_MODE", "1")


# ---------------------------------------------------------------------------
# 1. The switchboard
# ---------------------------------------------------------------------------

def test_demo_mode_flag_parsing(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", " 1 "):
        monkeypatch.setenv("LEADLENS_DEMO_MODE", value)
        assert demo_mode_enabled() is True
    for value in ("", "0", "false", "no", "off", "2"):
        monkeypatch.setenv("LEADLENS_DEMO_MODE", value)
        assert demo_mode_enabled() is False
    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    assert demo_mode_enabled() is False


def test_guards_are_complete_noops_when_demo_mode_is_off():
    assert_write_allowed("x")
    assert_external_action_allowed("x")
    assert_export_allowed("x")
    assert_upload_allowed("x")


def test_write_guard_refuses_in_demo_mode_and_allow_context_bypasses_only_writes(monkeypatch):
    _demo_on(monkeypatch)
    with pytest.raises(DemoModeError) as info:
        assert_write_allowed("thing")
    assert str(info.value) == DEMO_DISABLED_MESSAGE
    assert isinstance(info.value, RuntimeError)  # every LLM caller already handles RuntimeError

    with allow_demo_writes():
        assert_write_allowed("thing")  # operator scripts / Alembic only
    with pytest.raises(DemoModeError):
        assert_write_allowed("thing")  # and the allowance does not leak past the block


def test_external_export_and_upload_can_never_be_bypassed(monkeypatch):
    _demo_on(monkeypatch)
    with allow_demo_writes():  # even the operator context cannot enable these
        with pytest.raises(DemoModeError):
            assert_external_action_allowed("whatsapp")
        with pytest.raises(DemoModeError):
            assert_export_allowed("csv")
        with pytest.raises(DemoModeError):
            assert_upload_allowed("file")


# ---------------------------------------------------------------------------
# 2. Database-level write guard
# ---------------------------------------------------------------------------

BLOCKED = [
    "INSERT INTO patients (a) VALUES (?)",
    '  insert into "patients" (a) values (1)',
    "UPDATE organizations SET name = 'x'",
    "DELETE FROM memberships WHERE id = 1",
    "CREATE TABLE x (id integer)",
    "DROP TABLE x",
    "ALTER TABLE x ADD COLUMN y integer",
    "TRUNCATE x",
    "INSERT OR REPLACE INTO memory_store (id) VALUES (1)",
    "REPLACE INTO x VALUES (1)",
    "WITH t AS (SELECT 1) DELETE FROM x",
    "INSERT INTO public.patients (a) VALUES (1)",
    "UPDATE ONLY x SET a = 1",
]
ALLOWED = [
    "SELECT 1",
    "SELECT * FROM patients FOR UPDATE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT sa_savepoint_1",
    "RELEASE SAVEPOINT sa_savepoint_1",
    "PRAGMA foreign_keys=ON",
    "SELECT * FROM demo_usage",
    "INSERT INTO demo_usage (window_key, scope, count) VALUES (?, ?, ?)",
    "UPDATE demo_usage SET count = count + 1 WHERE id = 1",
    'INSERT INTO "demo_usage" (window_key) VALUES (?)',
    "UPDATE public.demo_usage SET count = 1",
]


@pytest.mark.parametrize("statement", BLOCKED)
def test_statement_classifier_blocks_writes_and_ddl(statement):
    assert demo_guard.statement_is_blocked(statement) is True


@pytest.mark.parametrize("statement", ALLOWED)
def test_statement_classifier_allows_reads_transaction_control_and_the_usage_table(statement):
    assert demo_guard.statement_is_blocked(statement) is False


def test_only_the_usage_table_is_writable_in_demo_mode():
    assert demo_guard.ALLOWED_WRITE_TABLES == frozenset({"demo_usage"})


def test_orm_writes_blocked_in_demo_mode_reads_still_work(engine, monkeypatch):
    with Session(engine) as session:
        session.add(Organization(name="Seeded", slug="seeded"))
        session.commit()

    _demo_on(monkeypatch)
    with Session(engine) as session:
        session.add(Organization(name="Intruder", slug="intruder"))
        with pytest.raises(DemoModeError):
            session.commit()
        session.rollback()
        assert session.query(Organization).count() == 1  # reads fine, write never landed
        # and the session is still usable afterwards
        assert session.query(Organization).filter_by(slug="seeded").one().name == "Seeded"


def test_updates_deletes_raw_sql_and_ddl_blocked_in_demo_mode(engine, monkeypatch):
    with Session(engine) as session:
        session.add(Organization(name="Seeded", slug="seeded"))
        session.commit()

    _demo_on(monkeypatch)
    with Session(engine) as session:
        org = session.query(Organization).one()
        org.name = "Renamed"
        with pytest.raises(DemoModeError):
            session.commit()
        session.rollback()

        session.delete(session.query(Organization).one())
        with pytest.raises(DemoModeError):
            session.commit()
        session.rollback()

        with pytest.raises(DemoModeError):
            session.execute(text("INSERT INTO organizations (name, slug, status, is_demo, created_at, updated_at) VALUES ('a','b','ACTIVE',0,'x','x')"))
        session.rollback()
        with pytest.raises(DemoModeError):
            session.execute(text("DROP TABLE organizations"))
        session.rollback()
    # DDL through an engine (no session involved) is blocked too. (create_all() on
    # tables that already exist emits no DDL, so use a genuinely new table.)
    with pytest.raises(DemoModeError):
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE evil (id integer)"))
    with pytest.raises(DemoModeError):
        Base.metadata.drop_all(engine)

    with Session(engine) as session:
        assert session.query(Organization).one().name == "Seeded"


def test_operator_writes_allowed_inside_allow_context(engine, monkeypatch):
    _demo_on(monkeypatch)
    with allow_demo_writes():
        with Session(engine) as session:
            session.add(Organization(name="Operator", slug="operator"))
            session.commit()
    with Session(engine) as session:
        assert session.query(Organization).count() == 1


def test_usage_counter_table_is_the_only_writable_table(engine, monkeypatch):
    _demo_on(monkeypatch)
    with Session(engine) as session:
        session.add(DemoUsageCounter(window_key="2026-09-26", scope="llm_calls", count=1))
        session.commit()  # allowed
        session.add(Organization(name="X", slug="x"))
        with pytest.raises(DemoModeError):
            session.commit()


def test_db_guard_is_inert_when_demo_mode_is_off(engine):
    with Session(engine) as session:
        session.add(Organization(name="Normal", slug="normal"))
        session.commit()
        org = session.query(Organization).one()
        org.name = "Renamed"
        session.commit()
        session.delete(org)
        session.commit()
        assert session.query(Organization).count() == 0


# ---------------------------------------------------------------------------
# 3. Schema, role, and the permission cap
# ---------------------------------------------------------------------------

def _make_membership(session, *, demo: bool, role: MembershipRole, slug: str, email: str):
    org = organization_service.create_organization(session, name=slug, slug=slug)
    org.is_demo = demo
    session.flush()
    user = user_service.create_user(session, email=email, password=PASSWORD)
    membership_service.create_membership(session, user_id=user.id, organization_id=org.id, role=role)
    session.commit()
    return org.id, user.id


def test_is_demo_defaults_to_false_for_every_organization(engine):
    with Session(engine) as session:
        org = organization_service.create_organization(session, name="Real Clinic", slug="real")
        session.commit()
        assert org.is_demo is False


def test_demo_viewer_role_is_read_only_and_within_the_taxonomy():
    assert MembershipRole.DEMO_VIEWER.value == "DEMO_VIEWER"
    assert DEMO_VIEWER_PERMISSIONS <= PERMISSIONS
    assert ROLE_PERMISSIONS[MembershipRole.DEMO_VIEWER] == DEMO_VIEWER_PERMISSIONS
    assert DEMO_ORG_PERMISSION_CAP == DEMO_VIEWER_PERMISSIONS
    for permission in DEMO_VIEWER_PERMISSIONS:
        assert not permission.endswith((".manage", ".approve"))
        assert not permission.startswith(("members.", "integrations.", "audit."))


def test_demo_viewer_can_view_what_the_demo_showcases():
    for permission in (
        "patients.view", "appointments.view", "leads.view", "payments.view", "finance.view",
        "automations.view", "jarvis.use", "jarvis.finance", "jarvis.operations", "jarvis.marketing",
    ):
        assert permission in DEMO_VIEWER_PERMISSIONS


def test_demo_viewer_lacks_every_mutating_or_sensitive_permission():
    forbidden = PERMISSIONS - DEMO_VIEWER_PERMISSIONS
    for permission in (
        "organization.manage", "members.view", "members.manage", "patients.manage",
        "appointments.manage", "treatments.manage", "payments.manage", "leads.manage",
        "automations.approve", "automations.manage", "integrations.view", "integrations.manage",
        "audit.view",
    ):
        assert permission in forbidden


def test_cap_is_a_noop_for_real_tenants_and_a_ceiling_for_demo_tenants():
    owner = frozenset(PERMISSIONS)
    assert cap_permissions_for_org(owner, False) == owner
    assert cap_permissions_for_org(owner, True) == DEMO_ORG_PERMISSION_CAP
    assert cap_permissions_for_org(frozenset({"patients.manage", "patients.view"}), True) == frozenset({"patients.view"})


def test_owner_inside_a_demo_org_is_capped_to_read_only(engine, monkeypatch):
    with Session(engine) as session:
        org_id, user_id = _make_membership(
            session, demo=True, role=MembershipRole.OWNER, slug="demo", email="owner@demo.invalid"
        )
    _demo_on(monkeypatch)
    with Session(engine) as session:
        decision = resolve_identity(session, user_id=user_id, organization_id=org_id)
        assert decision.allowed
        assert decision.identity.permissions == DEMO_ORG_PERMISSION_CAP
        for permission in ("organization.manage", "members.manage", "integrations.manage",
                           "patients.manage", "automations.approve"):
            assert authorize(session, user_id=user_id, organization_id=org_id, permission=permission).reason == "permission_denied"
        assert authorize(session, user_id=user_id, organization_id=org_id, permission="patients.view").allowed


def test_demo_viewer_gets_exactly_the_demo_set_in_a_demo_org(engine):
    with Session(engine) as session:
        org_id, user_id = _make_membership(
            session, demo=True, role=MembershipRole.DEMO_VIEWER, slug="demo", email="viewer@demo.invalid"
        )
        decision = resolve_identity(session, user_id=user_id, organization_id=org_id)
        assert decision.allowed
        assert decision.identity.permissions == DEMO_VIEWER_PERMISSIONS


def test_demo_viewer_role_is_refused_in_a_non_demo_org(engine):
    with Session(engine) as session:
        org_id, user_id = _make_membership(
            session, demo=False, role=MembershipRole.DEMO_VIEWER, slug="real", email="viewer@real.example"
        )
        decision = resolve_identity(session, user_id=user_id, organization_id=org_id)
        assert decision.allowed is False
        assert decision.reason == "demo_role_outside_demo_org"
        assert authorize(session, user_id=user_id, organization_id=org_id, permission="patients.view").allowed is False


def test_real_tenant_permissions_are_unchanged_by_the_demo_work(engine):
    with Session(engine) as session:
        org_id, user_id = _make_membership(
            session, demo=False, role=MembershipRole.OWNER, slug="real", email="owner@real.example"
        )
        decision = resolve_identity(session, user_id=user_id, organization_id=org_id)
        assert decision.identity.permissions == frozenset(PERMISSIONS)


def test_demo_user_cannot_reach_another_tenant_by_guessing_its_id(engine, monkeypatch):
    with Session(engine) as session:
        _, demo_user_id = _make_membership(
            session, demo=True, role=MembershipRole.DEMO_VIEWER, slug="demo", email="viewer@demo.invalid"
        )
        real_org_id, _ = _make_membership(
            session, demo=False, role=MembershipRole.OWNER, slug="real", email="owner@real.example"
        )
    _demo_on(monkeypatch)
    with Session(engine) as session:
        for permission in ("patients.view", "organization.view", "jarvis.use"):
            decision = authorize(session, user_id=demo_user_id, organization_id=real_org_id, permission=permission)
            assert decision.allowed is False
            assert decision.reason == "membership_not_found"
        assert build_user_context(session, user_id=demo_user_id, organization_id=real_org_id) is None


def test_tenant_context_carries_the_capped_permissions(engine):
    with Session(engine) as session:
        org_id, user_id = _make_membership(
            session, demo=True, role=MembershipRole.OWNER, slug="demo", email="owner@demo.invalid"
        )
        context = build_user_context(session, user_id=user_id, organization_id=org_id)
        assert context is not None
        assert context.permissions == DEMO_ORG_PERMISSION_CAP


def test_first_stored_login_session_is_capped_too(engine, monkeypatch):
    import core.auth as auth
    import core.identity.session as session_module

    with Session(engine) as session:
        org_id, user_id = _make_membership(
            session, demo=True, role=MembershipRole.OWNER, slug="demo", email="owner@demo.invalid"
        )
    captured = {}
    monkeypatch.setattr(session_module, "store_session", lambda s: captured.setdefault("session", s))
    monkeypatch.setattr(auth, "_clear_pending_membership_choice", lambda: None)
    with Session(engine) as session:
        user = user_service.get_user(session, user_id)
        membership = membership_service.get_membership_for_user_org(session, user_id=user_id, organization_id=org_id)
        auth._complete_login(session, user, membership)
    assert captured["session"].permissions == DEMO_ORG_PERMISSION_CAP


# ---------------------------------------------------------------------------
# 4. Legacy memory_store guard
# ---------------------------------------------------------------------------

@pytest.fixture()
def legacy_store(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    business_memory.ensure_database()
    return business_memory


def test_legacy_writes_refused_in_demo_mode_and_data_is_unchanged(legacy_store, monkeypatch):
    before = legacy_store.load_memory()["tasks"][:]
    _demo_on(monkeypatch)

    with pytest.raises(DemoModeError):
        legacy_store.update_memory(lambda m: m["tasks"].append({"title": "intruder"}))
    with pytest.raises(DemoModeError):
        legacy_store.add_task("Intruder task", "Ops")
    with pytest.raises(DemoModeError):
        legacy_store.save_memory({"company": {"hacked": True}})

    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    legacy_store._read_cache_at = 0  # drop the read cache so we see the database
    assert legacy_store.load_memory()["tasks"] == before


def test_read_only_mutator_still_works_in_demo_mode(legacy_store, monkeypatch):
    _demo_on(monkeypatch)
    result = legacy_store.update_memory(lambda m: legacy_store.NO_CHANGE)
    assert isinstance(result, dict)  # a mutator that decides "nothing to do" never writes, so never raises


def test_operator_context_can_write_the_legacy_store(legacy_store, monkeypatch):
    _demo_on(monkeypatch)
    with allow_demo_writes():
        legacy_store.add_task("Seeded task", "Ops")
    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    legacy_store._read_cache_at = 0
    assert any(t.get("data", {}).get("title") == "Seeded task" for t in legacy_store.load_memory()["tasks"])


def test_legacy_writes_work_normally_when_demo_mode_is_off(legacy_store):
    legacy_store.add_task("Normal task", "Ops")
    legacy_store._read_cache_at = 0
    assert any(t.get("data", {}).get("title") == "Normal task" for t in legacy_store.load_memory()["tasks"])


# ---------------------------------------------------------------------------
# 5. External actions
# ---------------------------------------------------------------------------

def _no_network(*args, **kwargs):
    raise AssertionError("a network call was attempted in demo mode")


def test_whatsapp_adapter_is_forced_dry_run_and_never_calls_the_network(monkeypatch):
    import integrations.whatsapp_service as wa

    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "REAL-LOOKING-TOKEN")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setattr(wa.requests, "post", _no_network)
    _demo_on(monkeypatch)

    service = wa.WhatsAppBusinessService(dry_run=False)  # even an explicit live request
    assert service.dry_run is True
    assert service.token == "" and service.phone_number_id == ""
    assert service.status()["mode"] == "dry-run"
    assert "REAL-LOOKING-TOKEN" not in repr(service.status())
    result = service.send_text({"to": "919999999999", "body": "hello"})
    assert result.success and result.status == "simulated"

    # Belt and braces: even if something flips dry_run off afterwards, the call site refuses.
    service.dry_run = False
    result = service.send_text({"to": "919999999999", "body": "hello"})
    assert result.success is False and result.status == "failed"
    assert result.detail == DEMO_DISABLED_MESSAGE


def test_whatsapp_adapter_is_unchanged_when_demo_mode_is_off(monkeypatch):
    import integrations.whatsapp_service as wa

    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    service = wa.WhatsAppBusinessService()
    assert service.token == "tok" and service.dry_run is False  # live when configured, as before


def test_gmail_and_calendar_adapters_are_forced_dry_run_and_never_build_a_client(monkeypatch, tmp_path):
    import googleapiclient.discovery as discovery
    import integrations.calendar_service as cal
    import integrations.gmail_service as gm

    key_file = tmp_path / "sa.json"
    key_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(key_file))
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "someone@example.com")
    monkeypatch.setattr(discovery, "build", _no_network)
    _demo_on(monkeypatch)

    mail = gm.GmailService(dry_run=False)
    assert mail.dry_run is True and mail.credentials_path == "" and mail.delegated_user == ""
    assert mail.send_email({"to": "a@example.com", "subject": "s", "body": "b"}).status == "simulated"
    mail.dry_run = False
    forced = mail.send_email({"to": "a@example.com", "subject": "s", "body": "b"})
    assert forced.success is False and forced.detail == DEMO_DISABLED_MESSAGE

    calendar = cal.GoogleCalendarService(dry_run=False)
    assert calendar.dry_run is True and calendar.credentials_path == ""
    event = {"summary": "s", "start": "2026-01-01T10:00:00", "end": "2026-01-01T11:00:00"}
    assert calendar.create_event(event).status == "simulated"
    calendar.dry_run = False
    forced = calendar.create_event(event)
    assert forced.success is False and forced.detail == DEMO_DISABLED_MESSAGE


def test_credential_factory_never_resolves_credentials_in_demo_mode(monkeypatch):
    import services.integration_clients as clients
    from core.identity.tenant_context import ActorType, TenantContext

    def _boom(*args, **kwargs):
        raise AssertionError("credential resolution was attempted in demo mode")

    monkeypatch.setattr(clients, "resolve_provider_credentials", _boom)
    _demo_on(monkeypatch)
    context = TenantContext(organization_id=1, actor_type=ActorType.SYSTEM)
    assert clients.get_whatsapp_client(context).dry_run is True
    assert clients.get_gmail_client(context).dry_run is True
    assert clients.get_calendar_client(context).dry_run is True


def test_queued_actions_never_execute_in_demo_mode(monkeypatch):
    from services.integration_manager_v21 import execute_item

    _demo_on(monkeypatch)
    result = execute_item("any-item-id")
    assert result == {"success": False, "status": "blocked", "detail": DEMO_DISABLED_MESSAGE}


def test_scheduler_never_runs_in_demo_mode(monkeypatch):
    import scheduler.run_scheduled_checks as sched

    _demo_on(monkeypatch)
    assert sched.run_all_checks() == {}
    assert sched.resolve_scheduler_organizations() == []
    assert sched.main() == 0


def test_scheduler_excludes_a_demo_org_that_shares_a_database(engine, monkeypatch):
    """Even a NON-demo deployment (e.g. production) must never schedule
    automations for an is_demo organization that somehow ended up in its DB."""
    import core.db.session as db_session
    import scheduler.run_scheduled_checks as sched

    with Session(engine) as session:
        real = organization_service.create_organization(session, name="Real", slug="real")
        demo = organization_service.create_organization(session, name="Demo", slug="demo")
        demo.is_demo = True
        session.flush()
        for org in (real, demo):
            session.add(OrganizationSettings(organization_id=org.id, automations_enabled=True))
        session.commit()
        real_id = real.id

    monkeypatch.setenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "true")
    monkeypatch.setattr(db_session, "make_engine", lambda *a, **k: engine)
    assert sched.resolve_scheduler_organizations() == [real_id]


def test_audit_events_are_not_persisted_in_demo_mode(monkeypatch):
    import services.security_service as security

    def _boom(*args, **kwargs):
        raise AssertionError("an audit row was persisted in demo mode")

    monkeypatch.setattr(security, "add_memory_entry", _boom)
    _demo_on(monkeypatch)
    security.audit_event("visitor", "view", "dashboard")  # returns quietly


# ---------------------------------------------------------------------------
# 6. LLM limiter
# ---------------------------------------------------------------------------

@pytest.fixture()
def llm_env(engine, monkeypatch):
    """Demo mode on, a fresh in-memory usage table, and an isolated per-session store."""
    monkeypatch.setattr(demo_llm, "_ENGINE", engine)
    session_store: dict = {}
    monkeypatch.setattr(demo_llm, "_session_store", lambda: session_store)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    _demo_on(monkeypatch)
    return session_store


class _FakeResponse:
    output_text = "A concise, grounded answer."
    status = "completed"
    incomplete_details = None


def _install_fake_openai(monkeypatch):
    import openai

    calls: list[dict] = []

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = self

        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeResponse()

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return calls


def test_llm_call_is_pinned_to_the_demo_model_capped_and_toolless(llm_env, monkeypatch):
    from services.ai import generate_ai_response

    calls = _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.1")
    monkeypatch.setenv("DEMO_OPENAI_MODEL", "demo-cheap-model")
    monkeypatch.setenv("DEMO_LLM_MAX_OUTPUT_TOKENS", "1500")

    answer = generate_ai_response("hi", "sys", model_name="gpt-5.1", max_output_tokens=9000)
    assert answer == "A concise, grounded answer."
    (kwargs,) = calls
    assert kwargs["model"] == "demo-cheap-model"  # per-call override ignored
    assert kwargs["max_output_tokens"] <= 1500
    assert kwargs["store"] is False
    assert "tools" not in kwargs and "tool_choice" not in kwargs  # the model cannot invoke anything


def test_llm_per_session_cap_fails_safe(llm_env, monkeypatch):
    from services.ai import generate_ai_response

    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_SESSION", "2")
    generate_ai_response("q1")
    generate_ai_response("q2")
    with pytest.raises(DemoUsageCapReached) as info:
        generate_ai_response("q3")
    assert info.value.scope == "session"
    assert str(info.value) == DEMO_USAGE_CAP_MESSAGE
    assert isinstance(info.value, RuntimeError)  # callers' existing `except RuntimeError` fallback handles it


def test_llm_global_daily_cap_spans_sessions(engine, monkeypatch):
    monkeypatch.setattr(demo_llm, "_ENGINE", engine)
    _demo_on(monkeypatch)
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_SESSION", "100")
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_HOUR", "100")
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_DAY", "3")
    now = datetime(2026, 9, 26, 14, 30, tzinfo=timezone.utc)

    for session_number in range(3):  # three DIFFERENT visitors share one budget
        monkeypatch.setattr(demo_llm, "_session_store", lambda: {})
        demo_llm.reserve_llm_call(engine=engine, now=now)
    monkeypatch.setattr(demo_llm, "_session_store", lambda: {})  # a fourth, brand-new session
    with pytest.raises(DemoUsageCapReached) as info:
        demo_llm.reserve_llm_call(engine=engine, now=now)
    assert info.value.scope == "day"

    # The next day starts a fresh window.
    demo_llm.reserve_llm_call(engine=engine, now=datetime(2026, 9, 27, 0, 5, tzinfo=timezone.utc))


def test_llm_global_hourly_cap(engine, monkeypatch):
    _demo_on(monkeypatch)
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_SESSION", "100")
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_DAY", "100")
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_HOUR", "2")
    monkeypatch.setattr(demo_llm, "_session_store", lambda: {})
    hour = datetime(2026, 9, 26, 14, 30, tzinfo=timezone.utc)
    demo_llm.reserve_llm_call(engine=engine, now=hour)
    demo_llm.reserve_llm_call(engine=engine, now=hour)
    with pytest.raises(DemoUsageCapReached) as info:
        demo_llm.reserve_llm_call(engine=engine, now=hour)
    assert info.value.scope == "hour"
    demo_llm.reserve_llm_call(engine=engine, now=datetime(2026, 9, 26, 15, 1, tzinfo=timezone.utc))


def test_llm_counter_takes_exactly_cap_units_then_refuses(engine, monkeypatch):
    _demo_on(monkeypatch)
    granted = [demo_llm._consume(engine, "2026-09-26", "llm_calls", 5) for _ in range(12)]
    assert granted.count(True) == 5 and granted.count(False) == 7
    assert demo_llm._consume(engine, "2026-09-26", "llm_calls", 0) is False  # a cap of 0 disables it


def test_llm_limiter_fails_closed_when_the_counter_is_unavailable(monkeypatch):
    class _Broken:
        def __getattr__(self, name):
            raise RuntimeError("database down")

    _demo_on(monkeypatch)
    monkeypatch.setattr(demo_llm, "_session_store", lambda: {})
    with pytest.raises(DemoUsageCapReached) as info:
        demo_llm.reserve_llm_call(engine=_Broken())
    assert info.value.scope == "unavailable"


def test_malformed_cap_env_falls_back_to_the_default_not_to_unlimited(monkeypatch):
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_SESSION", "not-a-number")
    assert demo_llm._int_env("DEMO_LLM_MAX_CALLS_PER_SESSION", 20) == 20


def test_oversized_prompts_are_refused(monkeypatch):
    monkeypatch.setenv("DEMO_LLM_MAX_PROMPT_CHARS", "1000")
    demo_llm.check_prompt_size("a" * 500, "b" * 400)
    with pytest.raises(DemoModeError):
        demo_llm.check_prompt_size("a" * 900, "b" * 200)


def test_llm_path_is_completely_unchanged_when_demo_mode_is_off(monkeypatch):
    from services.ai import generate_ai_response

    def _boom(*args, **kwargs):
        raise AssertionError("the demo limiter ran while demo mode was off")

    monkeypatch.setattr(demo_llm, "reserve_llm_call", _boom)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    calls = _install_fake_openai(monkeypatch)
    generate_ai_response("hi", "sys", model_name="some-other-model", max_output_tokens=9000)
    assert calls[0]["model"] == "some-other-model"
    assert calls[0]["max_output_tokens"] == 9000


# ---------------------------------------------------------------------------
# 7. Exports and uploads
# ---------------------------------------------------------------------------

class _FakeStreamlit:
    def __init__(self):
        self.calls: list[tuple] = []

    def download_button(self, label, data=None, **kwargs):
        self.calls.append((label, data, kwargs))
        return False


def test_download_button_never_builds_bytes_in_demo_mode(monkeypatch):
    import ui.demo_gate as gate

    fake = _FakeStreamlit()
    monkeypatch.setattr(gate, "st", fake)
    built = []

    def factory():
        built.append(True)
        return b"SENSITIVE-EXPORT"

    _demo_on(monkeypatch)
    gate.guarded_download_button("Download everything", factory, file_name="x.csv", key="k")
    assert built == []  # the factory was never even called
    (label, data, kwargs), = fake.calls
    assert data == b"" and kwargs["disabled"] is True
    assert kwargs["help"] == DEMO_DISABLED_MESSAGE


def test_download_button_is_unchanged_when_demo_mode_is_off(monkeypatch):
    import ui.demo_gate as gate

    fake = _FakeStreamlit()
    monkeypatch.setattr(gate, "st", fake)
    gate.guarded_download_button("Download", lambda: b"payload", file_name="x.csv", key="k")
    (label, data, kwargs), = fake.calls
    assert data == b"payload" and "disabled" not in kwargs


def test_export_builders_refuse_server_side(monkeypatch):
    from ui.crm_dashboard import _csv_bytes

    csv_bytes = _csv_bytes([{"a": 1}])  # normal behavior when off (utf-8-sig, so it starts with a BOM)
    assert csv_bytes.decode("utf-8-sig").split() == ["a", "1"]
    _demo_on(monkeypatch)
    with pytest.raises(DemoModeError):
        _csv_bytes([{"a": 1}])


def test_uploads_refused_server_side_and_nothing_touches_the_disk(tmp_path, monkeypatch):
    import services.platform_data as platform_data

    monkeypatch.setattr(platform_data, "UPLOADS", tmp_path / "uploads")
    _demo_on(monkeypatch)
    with pytest.raises(DemoModeError):
        platform_data.save_uploaded_file("evil.csv", b"a,b\n1,2\n")
    assert not (tmp_path / "uploads").exists()

    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    path = platform_data.save_uploaded_file("ok.csv", b"a,b\n1,2\n")
    assert path.read_bytes() == b"a,b\n1,2\n"


# ---------------------------------------------------------------------------
# 8. File writes and repo-tracked runtime files
# ---------------------------------------------------------------------------

def test_learning_memory_file_write_refused_in_demo_mode(tmp_path, monkeypatch):
    import services.jarvis_memory as jm

    monkeypatch.setattr(jm, "STORE", tmp_path / "learning.json")
    _demo_on(monkeypatch)
    with pytest.raises(DemoModeError):
        jm._write_json_compat({"schema_version": 3})
    assert not (tmp_path / "learning.json").exists()

    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    jm._write_json_compat({"schema_version": 3})
    assert (tmp_path / "learning.json").exists()


def test_council_session_persistence_is_a_noop_in_demo_mode(tmp_path, monkeypatch):
    import services.agent_collaboration_v23 as council

    monkeypatch.setattr(council, "STORE", tmp_path / "council.json")
    _demo_on(monkeypatch)
    council._save_session({"id": "s1"})
    assert not (tmp_path / "council.json").exists()

    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    council._save_session({"id": "s2"})
    assert (tmp_path / "council.json").exists()


def _resolved_paths(env_overrides: dict[str, str]) -> dict[str, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), **env_overrides}
    code = (
        "import services.jarvis_memory as m, services.jarvis_context as c, "
        "services.agent_collaboration_v23 as a; "
        "print(m.STORE); print(c.LEARNING_FILE); print(a.STORE)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=True,
    ).stdout.split("\n")
    return {"memory": out[0].strip(), "context": out[1].strip(), "council": out[2].strip()}


def test_demo_mode_never_reads_the_repo_tracked_runtime_files():
    demo = _resolved_paths({"LEADLENS_DEMO_MODE": "1", "LEADLENS_LEARNING_MEMORY_PATH": "/tmp/attacker.json"})
    for path in demo.values():
        normalized = path.replace("\\", "/")
        assert "/demo/seed/" in normalized, path
        assert "/data/" not in normalized, path  # the checkout's own data/ files are never read
    assert "attacker" not in demo["memory"]  # the env override is ignored in demo mode

    normal = _resolved_paths({"LEADLENS_DEMO_MODE": ""})
    assert normal["context"].replace("\\", "/").endswith("data/learning/learning_memory.json")
    assert normal["council"].replace("\\", "/").endswith("data/collaboration/council_sessions.json")


# ---------------------------------------------------------------------------
# 9. Startup tripwire
# ---------------------------------------------------------------------------

from core.demo_tripwire import (  # noqa: E402
    DemoEnvironmentError,
    database_problems,
    demo_seeded,
    enforce_environment,
    environment_problems,
)

def _pg_url(user: str, password: str, host: str, database: str, scheme: str = "postgresql") -> str:
    """Built by concatenation on purpose: the repo's pre-commit secret scan refuses any literal
    scheme://user:password@host in source, and these tests need SENTINEL passwords."""
    return scheme + "://" + user + ":" + password + "@" + host + "/" + database


GOOD_ENV = {
    "LEADLENS_DEMO_MODE": "1",
    "LEADLENS_V2_AUTH_ENABLED": "1",
    "LEADLENS_V2_TENANT_CONTEXT_ENABLED": "1",
    "LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED": "1",
    "LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED": "1",
    "LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED": "1",
    "DATABASE_URL": _pg_url("demo_user", "DEMO-DB-PASSWORD", "demo-host.example", "demo_db"),
    "LEADLENS_DEFAULT_ORG_SLUG": "leadlens-demo",
    "OPENAI_API_KEY": "sk-demo-only-sentinel",
}


def test_a_correct_demo_environment_passes():
    assert environment_problems(GOOD_ENV) == []
    enforce_environment(GOOD_ENV)


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ({"LEADLENS_DEMO_MODE": ""}, "LEADLENS_DEMO_MODE"),
        ({"WHATSAPP_ACCESS_TOKEN": "SECRET-SENTINEL-1"}, "WHATSAPP_ACCESS_TOKEN"),
        ({"GMAIL_DELEGATED_USER": "SECRET-SENTINEL-1"}, "GMAIL_DELEGATED_USER"),
        ({"GOOGLE_SERVICE_ACCOUNT_FILE": "/x/y.json"}, "GOOGLE_SERVICE_ACCOUNT_FILE"),
        ({"MASTER_DATABASE_URL": "postgresql://prod"}, "MASTER_DATABASE_URL"),
        ({"CLIENT1_DATABASE_URL": "postgresql://prod"}, "CLIENT1_DATABASE_URL"),
        ({"LEADLENS_CREDENTIAL_ENCRYPTION_KEY": "SECRET-SENTINEL-1"}, "LEADLENS_CREDENTIAL_ENCRYPTION_KEY"),
        ({"APP_PASSWORD": "SECRET-SENTINEL-1"}, "APP_PASSWORD"),
        ({"LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED": "true"}, "LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED"),
        ({"LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED": "true"}, "LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED"),
        ({"LEADLENS_V2_AUTH_ENABLED": ""}, "LEADLENS_V2_AUTH_ENABLED"),
        ({"LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED": ""}, "LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED"),
        ({"DATABASE_URL": ""}, "DATABASE_URL"),
        ({"DATABASE_URL": "sqlite:///demo.db"}, "plain postgresql://"),
        ({"DATABASE_URL": _pg_url("u", "pw", "h", "db", "postgresql+psycopg")}, "plain postgresql://"),  # psycopg2 in the legacy store cannot parse it
        ({"LEADLENS_V2_DATABASE_URL": "postgresql://other/db"}, "different databases"),
        ({"LEADLENS_DEFAULT_ORG_SLUG": "default-clinic"}, "LEADLENS_DEFAULT_ORG_SLUG"),
        ({"OPENAI_API_KEY": ""}, "OPENAI_API_KEY"),
    ],
)
def test_tripwire_flags_each_unsafe_configuration(mutation, expected):
    problems = environment_problems({**GOOD_ENV, **mutation})
    assert problems, mutation
    assert any(expected in p for p in problems), (expected, problems)
    with pytest.raises(DemoEnvironmentError):
        enforce_environment({**GOOD_ENV, **mutation})


def test_tripwire_never_reveals_a_secret_value():
    env = {
        **GOOD_ENV,
        "WHATSAPP_ACCESS_TOKEN": "SECRET-SENTINEL-1",
        "APP_PASSWORD": "SECRET-SENTINEL-2",
        "MASTER_DATABASE_URL": _pg_url("user", "SECRET-SENTINEL-3", "prod", "db"),
    }
    rendered = " | ".join(environment_problems(env))
    assert "WHATSAPP_ACCESS_TOKEN" in rendered and "APP_PASSWORD" in rendered
    for sentinel in ("SECRET-SENTINEL-1", "SECRET-SENTINEL-2", "SECRET-SENTINEL-3", "DEMO-DB-PASSWORD", "sk-demo-only-sentinel"):
        assert sentinel not in rendered


def test_tripwire_database_checks(engine):
    with Session(engine) as session:
        assert database_problems(session) == []  # an empty database is just "not seeded yet"
        assert demo_seeded(session) is False

        demo = organization_service.create_organization(session, name="LeadLens Demo Clinic", slug="leadlens-demo")
        demo.is_demo = True
        session.flush()
        assert database_problems(session) == []
        assert demo_seeded(session) is False  # no demo viewer yet
        user = user_service.create_user(session, email="viewer@demo.leadlens.invalid", password=PASSWORD)
        membership_service.create_membership(
            session, user_id=user.id, organization_id=demo.id, role=MembershipRole.DEMO_VIEWER
        )
        session.commit()
        assert demo_seeded(session) is True
        assert database_problems(session, {"business_name": "LeadLens Demo Clinic", "is_demo": True}) == []

        # A production-looking database: a real organization, or an unmarked company profile.
        assert database_problems(session, {"business_name": "Riverside Physio"})
        organization_service.create_organization(session, name="Real Clinic", slug="real")
        session.commit()
        assert any("non-demo organization" in p for p in database_problems(session))


# ---------------------------------------------------------------------------
# 10. Source-level invariants — a future unguarded path fails here
# ---------------------------------------------------------------------------

_APP_DIRS = ("core", "services", "ui", "integrations", "scheduler", "workflows")
_APP_FILES = ("app.py", "dashboard.py", "onboarding.py")


def _app_sources():
    for directory in _APP_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path
    for name in _APP_FILES:
        yield ROOT / name


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_application_code_never_enters_the_operator_write_context():
    # demo_mode.py defines it; demo_guard.py only MENTIONS it in its docstring.
    offenders = [
        str(p.relative_to(ROOT))
        for p in _app_sources()
        if "allow_demo_writes" in _read(p) and p.name not in {"demo_mode.py", "demo_guard.py"}
    ]
    assert offenders == [], f"only scripts/, tests/ and alembic/env.py may use allow_demo_writes: {offenders}"
    guard_source = _read(ROOT / "core" / "db" / "demo_guard.py")
    usages = [
        line for line in guard_source.splitlines()
        if "allow_demo_writes" in line and line.lstrip().startswith(("from ", "import ", "with "))
    ]
    assert usages == []


def test_downloads_and_uploads_only_go_through_the_guarded_helpers():
    raw_download = [
        str(p.relative_to(ROOT))
        for p in _app_sources()
        if "st.download_button(" in _read(p) and p.name != "demo_gate.py"
        # ui/chief_of_staff_workspace.py imports a nonexistent `agents` package and is unreachable dead code.
        and p.name != "chief_of_staff_workspace.py"
    ]
    assert raw_download == []

    hub = _read(ROOT / "ui" / "data_hub.py")
    assert hub.index("demo_mode_enabled()") < hub.index("st.file_uploader(")  # the uploader is inside the demo-gated branch
    uploaders = [str(p.relative_to(ROOT)) for p in _app_sources() if "st.file_uploader(" in _read(p)]
    assert uploaders == [str(Path("ui") / "data_hub.py")]


def test_outbound_network_calls_exist_only_in_the_reviewed_places():
    """The public demo's safety story depends on this exact inventory. A new
    outbound call (HTTP, SMTP, Google APIs, another LLM client) must be
    reviewed for demo-mode handling before this list is extended."""
    expectations = {
        "requests.post(": {"integrations/whatsapp_service.py"},
        "requests.get(": set(),
        "requests.put(": set(),
        "requests.delete(": set(),
        "requests.request(": set(),
        "smtplib": set(),
        "urlopen(": set(),
        "httpx.": set(),
        ".messages().send(": {"integrations/gmail_service.py"},
        "events().insert(": {"integrations/calendar_service.py"},
        "OpenAI(": {"services/ai.py"},
    }
    for needle, allowed in expectations.items():
        found = {str(p.relative_to(ROOT)).replace("\\", "/") for p in _app_sources() if needle in _read(p)}
        assert found == allowed, f"{needle!r} appears in {sorted(found)}, expected {sorted(allowed)}"


def test_adapters_and_llm_entry_point_all_consult_the_demo_switchboard():
    for relative in ("integrations/whatsapp_service.py", "integrations/gmail_service.py",
                     "integrations/calendar_service.py", "services/ai.py"):
        assert "demo_mode_enabled" in _read(ROOT / relative), relative
    for relative in ("integrations/whatsapp_service.py", "integrations/gmail_service.py",
                     "integrations/calendar_service.py"):
        assert "assert_external_action_allowed" in _read(ROOT / relative), relative
