"""Live audit of a demo deployment: tries to break every promise the demo makes.

Runs against a REAL demo database and the operator's demo environment (see
scripts/audit_demo_deployment.py, which adds the same target safety checks
the seed/reset commands use). Every attack is expected to be REFUSED; the
audit fails if any succeeds, if anything changes, or if a secret shows up in
any output. The engine is injectable so the same audit runs in the test
suite against a throwaway database.

What is attacked, mapped to the deployment-verification goals:

  production tenant data   the demo identity cannot authorize into any other
                           organization; the database is checked to hold no
                           non-demo organization at all
  bypass demo permissions  a demo user cannot authenticate with any password;
                           the session it is given is capped read-only
  external actions         every adapter, with provider credentials PLANTED in
                           the environment, stays dry-run and makes no call
  secrets                  planted secret values never appear in any result
  modify users/settings/   ORM, Core, raw-SQL and DDL writes, and the legacy
  integrations             store, are all refused; nothing changes
  export / upload          both refused server-side
  escape the demo tenant   cross-organization authorization attempts fail
  exceed LLM limits        session, hourly and daily caps refuse; the limiter
                           fails closed when its counter is unavailable
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

# Planted in the environment during the audit; must never appear in any output.
_PLANTED = {
    "WHATSAPP_ACCESS_TOKEN": "AUDIT-PLANTED-WA-TOKEN-0f3c",
    "WHATSAPP_PHONE_NUMBER_ID": "AUDIT-PLANTED-WA-PHONE-0f3c",
    "GMAIL_DELEGATED_USER": "audit-planted-0f3c@example.com",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "AUDIT-PLANTED-GOOGLE-FILE-0f3c.json",
    "LEADLENS_CREDENTIAL_ENCRYPTION_KEY": "AUDIT-PLANTED-ENC-KEY-0f3c",
}
_LONG_AGO = datetime(1999, 1, 1, 12, 30, tzinfo=timezone.utc)  # a window no real visitor ever uses


@dataclass
class AuditResult:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    detail: str = ""


@contextmanager
def _env(**overrides: str) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _fingerprint(engine) -> dict:
    """Row counts of every table (except the LLM counters) + a hash of the legacy store."""
    from core.db.base import Base
    from core.memory import load_memory

    counts: dict[str, int] = {}
    with Session(engine) as session:
        for table in Base.metadata.sorted_tables:
            if table.name == "demo_usage":
                continue
            counts[table.name] = session.execute(select(func.count()).select_from(table)).scalar()
    legacy = hashlib.sha256(json.dumps(load_memory(), sort_keys=True, default=str).encode()).hexdigest()
    return {"tables": counts, "legacy": legacy}


def run_audit(
    engine,
    *,
    out: Callable[[str], None] = print,
    live_llm: bool = False,
    migration_state: Callable[[object], tuple[str | None, str | None]] | None = None,
) -> list[AuditResult]:
    import core.memory as legacy_memory
    from core import demo_llm
    from core.db.models.identity import User
    from core.demo_mode import (
        DEMO_ORG_SLUG, DEMO_USER_EMAIL, DemoModeError, DemoUsageCapReached, allow_demo_writes,
        assert_export_allowed, assert_external_action_allowed, assert_upload_allowed,
    )
    from core.demo_tripwire import database_problems, demo_seeded, environment_problems

    results: list[AuditResult] = []

    def record(name: str, ok: bool, detail: str = "", *, skip: bool = False) -> None:
        results.append(AuditResult(name, "SKIP" if skip else ("PASS" if ok else "FAIL"), detail))
        out(f"  [{results[-1].status}] {name}" + (f" — {detail}" if detail else ""))

    def attempt(name: str, fn: Callable[[], object]) -> None:
        """An attack: it must be refused with DemoModeError."""
        try:
            fn()
        except DemoModeError:
            record(name, True, "refused")
        except Exception as exc:  # noqa: BLE001 - a different failure is not the guard's refusal
            record(name, False, f"failed for a different reason: {type(exc).__name__}")
        else:
            record(name, False, "SUCCEEDED — the guard did not stop it")

    out("== environment and database ==")
    problems = environment_problems()
    record("environment passes the startup tripwire", not problems, "; ".join(problems))
    if migration_state is not None:
        current, head = migration_state(engine)
        record("migrations at head", current == head, f"database={current} code={head}")
    with Session(engine) as session:
        db_problems = database_problems(session, dict(legacy_memory.load_memory().get("company") or {}))
        record("database holds no non-demo organization and a demo-marked company profile", not db_problems,
               "; ".join(db_problems))
        seeded = demo_seeded(session)
        record("demo tenant is seeded", seeded)
    if db_problems or not seeded:
        # Never run attacks against a database that is not a seeded, demo-only one.
        record("audit aborted before any attack", False,
               "the database is not a seeded, demo-only database — fix that first", skip=False)
        return results
    driver = engine.dialect.driver
    record("SQLAlchemy driver in use", True, f"{engine.dialect.name}/{driver}")
    if engine.dialect.name != "postgresql":
        record("PostgreSQL in use", False, "the audit was pointed at a non-PostgreSQL database", skip=True)

    before = _fingerprint(engine)

    out("== identity: bypass demo permissions / escape the tenant ==")
    from core.db.models.organization import Organization
    from core.demo_session import build_demo_session
    from core.identity.authentication_service import authenticate
    from core.identity.authorization_service import authorize
    from core.identity.permissions import DEMO_ORG_PERMISSION_CAP

    with Session(engine) as session:
        outcomes = [authenticate(session, email=DEMO_USER_EMAIL, password=p).success
                    for p in ("", "password", "demo", "admin", DEMO_USER_EMAIL, "!demo-account-has-no-password")]
        record("the demo user cannot authenticate with any password", not any(outcomes))
        with _env(LEADLENS_DEMO_MODE="1"):
            demo_session = build_demo_session(session)
        record("the admitted session is capped to the read-only demo set",
               demo_session.permissions == DEMO_ORG_PERMISSION_CAP and demo_session.role.value == "DEMO_VIEWER",
               f"{len(demo_session.permissions)} permissions")
        forbidden = ("organization.manage", "members.manage", "integrations.manage", "integrations.view",
                     "patients.manage", "automations.approve", "audit.view")
        record("no write/approve/manage/integration/audit permission is held",
               not any(p in demo_session.permissions for p in forbidden))
        user = session.query(User).filter(User.email == DEMO_USER_EMAIL).one()
        other_ids = [o.id for o in session.query(Organization).filter(Organization.slug != DEMO_ORG_SLUG)]
        probe_ids = other_ids + [987654, 987655]  # any real organization, and ids that should not exist
        record("the demo user is authorized into no organization other than the demo",
               all(not authorize(session, user_id=user.id, organization_id=oid, permission="patients.view").allowed
                   for oid in probe_ids), f"probed {len(probe_ids)} organization ids")

    out("== writes: modify users / settings / integrations (demo mode on, RBAC ignored) ==")
    from core.db.models.clinic import Patient

    with _env(LEADLENS_DEMO_MODE="1"):
        def orm_insert():
            with Session(engine) as s:
                s.add(Organization(name="Audit Intruder", slug="audit-intruder"))
                s.commit()

        def orm_update():
            with Session(engine) as s:
                org = s.query(Organization).filter_by(slug=DEMO_ORG_SLUG).one()
                org.name = "Hijacked"
                s.commit()

        def orm_delete():
            with Session(engine) as s:
                s.delete(s.query(User).filter_by(email=DEMO_USER_EMAIL).one())
                s.commit()

        def raw(sql: str):
            def run():
                with Session(engine) as s:
                    s.execute(text(sql))
                    s.commit()
            return run

        attempt("ORM insert (create a user / organization)", orm_insert)
        attempt("ORM update (modify settings / the demo organization)", orm_update)
        attempt("ORM delete (delete the demo user)", orm_delete)
        # The WHERE clauses match nothing, so even a failure of the guard could not change data.
        attempt("raw SQL UPDATE", raw("UPDATE users SET status = 'DISABLED' WHERE 1 = 0"))
        attempt("raw SQL DELETE", raw("DELETE FROM organization_integrations WHERE 1 = 0"))
        attempt("raw SQL INSERT into an integration table", raw(
            "INSERT INTO organization_integrations (organization_id) SELECT 1 WHERE 1 = 0"))
        attempt("DDL (create a table)", raw("CREATE TABLE audit_probe_never_created (id integer)"))
        attempt("DDL (drop a table)", raw("DROP TABLE demo_usage_probe_never_existed"))
        attempt("legacy store write (settings / tasks / approvals)",
                lambda: legacy_memory.update_memory(lambda m: m.setdefault("audit_probe", 1)))
        attempt("legacy store overwrite", lambda: legacy_memory.save_memory({"company": {"is_demo": True}}))

        def configure_integration():
            from core.db.models.integration import IntegrationProvider
            from core.identity.tenant_context import ActorType, TenantContext
            from services.integration_credentials import configure_integration as configure

            with Session(engine) as s:
                org = s.query(Organization).filter_by(slug=DEMO_ORG_SLUG).one()
                configure(s, TenantContext(organization_id=org.id, actor_type=ActorType.SYSTEM),
                          IntegrationProvider.WHATSAPP, secret_fields={"access_token": "x"})
                s.commit()

        with _env(LEADLENS_CREDENTIAL_ENCRYPTION_KEY=_fernet_key()):
            attempt("configure a WhatsApp integration with a secret", configure_integration)

    after_writes = _fingerprint(engine)
    record("nothing changed after every write attempt", after_writes == before)

    out("== external actions and secrets (credentials PLANTED in the environment) ==")
    import googleapiclient.discovery as discovery
    import integrations.calendar_service as calendar
    import integrations.gmail_service as gmail
    import integrations.whatsapp_service as whatsapp
    import requests

    calls: list[str] = []

    def fail_on_call(*a, **k):  # noqa: ANN002
        calls.append("network call")
        raise AssertionError("network call attempted")

    originals = (requests.post, discovery.build)
    requests.post, discovery.build = fail_on_call, fail_on_call
    rendered = ""
    try:
        with _env(LEADLENS_DEMO_MODE="1", **_PLANTED):
            wa = whatsapp.WhatsAppBusinessService(dry_run=False)
            gm = gmail.GmailService(dry_run=False)
            cal = calendar.GoogleCalendarService(dry_run=False)
            simulated = [
                wa.send_text({"to": "911111111111", "body": "audit"}).status,
                gm.send_email({"to": "a@example.com", "subject": "s", "body": "b"}).status,
                gm.create_draft({"to": "a@example.com", "subject": "s", "body": "b"}).status,
                cal.create_event({"summary": "s", "start": "2026-01-01T10:00:00", "end": "2026-01-01T11:00:00"}).status,
            ]
            rendered = json.dumps([wa.status(), gm.status(), cal.status(), simulated], default=str)
            attempt("external action guard (WhatsApp/Gmail/Calendar)", lambda: assert_external_action_allowed("audit"))
    finally:
        requests.post, discovery.build = originals
    record("every adapter stayed dry-run and made no network call, despite planted credentials",
           simulated == ["simulated"] * 4 and not calls and wa.dry_run and gm.dry_run and cal.dry_run, f"results={simulated}")
    record("no planted secret value appears in any adapter output",
           not any(v in rendered for v in _PLANTED.values()))

    from services.integration_manager_v21 import execute_item

    with _env(LEADLENS_DEMO_MODE="1"):
        blocked = execute_item("audit-any-id")
    record("queued actions never execute", blocked.get("status") == "blocked" and blocked.get("success") is False)

    out("== export / upload ==")
    from services.platform_data import save_uploaded_file

    with _env(LEADLENS_DEMO_MODE="1"):
        attempt("export refused", lambda: assert_export_allowed("audit"))
        attempt("upload refused (never touches disk)", lambda: save_uploaded_file("audit.csv", b"a,b\n1,2\n"))

    out("== LLM usage limits (counted in a 1999 window, cleaned up afterwards) ==")
    store: dict = {}
    real_store = demo_llm._session_store
    demo_llm._session_store = lambda: store
    try:
        with _env(LEADLENS_DEMO_MODE="1", DEMO_LLM_MAX_CALLS_PER_SESSION="100",
                  DEMO_LLM_MAX_CALLS_PER_HOUR="100", DEMO_LLM_MAX_CALLS_PER_DAY="2"):
            demo_llm.reserve_llm_call(engine=engine, now=_LONG_AGO)
            demo_llm.reserve_llm_call(engine=engine, now=_LONG_AGO)
            attempt("daily cap refuses the call after the limit", lambda: demo_llm.reserve_llm_call(engine=engine, now=_LONG_AGO))
        with _env(LEADLENS_DEMO_MODE="1", DEMO_LLM_MAX_CALLS_PER_SESSION="100",
                  DEMO_LLM_MAX_CALLS_PER_HOUR="1", DEMO_LLM_MAX_CALLS_PER_DAY="100"):
            hour = datetime(1999, 1, 2, 3, 30, tzinfo=timezone.utc)
            demo_llm.reserve_llm_call(engine=engine, now=hour)
            capped = False
            try:
                demo_llm.reserve_llm_call(engine=engine, now=hour)
            except DemoUsageCapReached as exc:
                capped = exc.scope == "hour"
            record("hourly cap refuses the call after the limit", capped)
        store.clear()
        with _env(LEADLENS_DEMO_MODE="1", DEMO_LLM_MAX_CALLS_PER_SESSION="1",
                  DEMO_LLM_MAX_CALLS_PER_HOUR="100", DEMO_LLM_MAX_CALLS_PER_DAY="100"):
            other = datetime(1999, 2, 1, 3, 30, tzinfo=timezone.utc)
            demo_llm.reserve_llm_call(engine=engine, now=other)
            attempt("per-session cap refuses the call after the limit", lambda: demo_llm.reserve_llm_call(engine=engine, now=other))

        class _Broken:
            def __getattr__(self, name):
                raise RuntimeError("counter unavailable")

        store.clear()
        with _env(LEADLENS_DEMO_MODE="1"):
            attempt("the limiter fails CLOSED when its counter is unavailable",
                    lambda: demo_llm.reserve_llm_call(engine=_Broken(), now=_LONG_AGO))
    finally:
        demo_llm._session_store = real_store
        from core.db.models.demo import DemoUsageCounter

        with allow_demo_writes(), Session(engine) as session:
            session.query(DemoUsageCounter).filter(DemoUsageCounter.window_key.like("1999%")).delete(synchronize_session=False)
            session.commit()

    if live_llm:
        out("== live LLM smoke test (spends a fraction of a cent) ==")
        from services.ai import generate_ai_response

        try:
            with _env(LEADLENS_DEMO_MODE="1"):
                answer = generate_ai_response("Reply with the single word: ok", "Answer in one word.", max_output_tokens=1500)
            record("live LLM call through the demo limiter", bool(answer.strip()), f"model={demo_llm.demo_model()}")
        except Exception as exc:  # noqa: BLE001
            record("live LLM call through the demo limiter", False, f"{type(exc).__name__}: {str(exc)[:160]}")
    else:
        record("live LLM call through the demo limiter", False, "not run (pass --live-llm)", skip=True)

    final = _fingerprint(engine)
    record("nothing outside the LLM counters changed during the whole audit", final == before)
    return results


def _fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def summarize(results: list[AuditResult]) -> tuple[bool, str]:
    failed = [r for r in results if r.status == "FAIL"]
    passed = sum(r.status == "PASS" for r in results)
    skipped = sum(r.status == "SKIP" for r in results)
    return (not failed), f"{passed} passed, {len(failed)} failed, {skipped} skipped"
