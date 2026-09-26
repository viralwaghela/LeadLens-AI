"""V2 Phase 10 — tests for the live-deployment audit itself (demo/audit.py).

An audit that cannot fail proves nothing, so besides "it passes on a correct
demo database" this file proves it FAILS when a guard is broken, and that it
REFUSES to attack a database that looks like production.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

import core.demo_tripwire as tripwire
import core.memory as business_memory
from core.db.base import Base
import core.db.models  # noqa: F401
from core.db.models.demo import DemoUsageCounter
from core.db.session import make_engine
from core.demo_mode import allow_demo_writes
from core.identity import organization_service
from demo import audit
from demo.audit import run_audit, summarize
from demo.seeder import seed_demo


@pytest.fixture()
def seeded(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    seed_demo(engine, anchor=date(2026, 9, 26), seed_dir=tmp / "seed")
    monkeypatch.setattr(tripwire, "environment_problems", lambda env=None: [])  # SQLite test DB; the tripwire has its own tests
    yield engine
    engine.dispose()


def _run(engine):
    lines: list[str] = []
    results = run_audit(engine, out=lines.append, migration_state=lambda e: ("head", "head"))
    return results, "\n".join(lines)


def test_audit_passes_on_a_correct_demo_database(seeded):
    results, output = _run(seeded)
    failed = [r for r in results if r.status == "FAIL"]
    assert failed == [], [(r.name, r.detail) for r in failed]
    ok, summary = summarize(results)
    assert ok, summary
    assert len(results) >= 25  # it really ran the whole battery
    # Every attack category from the deployment-verification brief is present.
    names = " | ".join(r.name for r in results)
    for needle in ("cannot authenticate", "ORM insert", "raw SQL", "DDL", "legacy store write", "external action",
                   "planted secret", "export refused", "upload refused", "daily cap", "hourly cap", "per-session cap",
                   "fails CLOSED", "no organization other than the demo"):
        assert needle in names, needle


def test_audit_leaves_no_trace_and_never_prints_a_planted_secret(seeded):
    with Session(seeded) as session:
        before = session.query(DemoUsageCounter).count()
    results, output = _run(seeded)
    for value in audit._PLANTED.values():
        assert value not in output
    with Session(seeded) as session:
        assert session.query(DemoUsageCounter).count() == before  # its 1999 counter rows were cleaned up


def test_audit_fails_loudly_if_the_write_guard_is_broken(seeded, monkeypatch):
    import core.db.demo_guard as guard

    monkeypatch.setattr(guard, "statement_is_blocked", lambda statement: False)  # simulate a defeated guard
    results, _ = _run(seeded)
    ok, summary = summarize(results)
    assert not ok, summary
    failing = " | ".join(r.name for r in results if r.status == "FAIL")
    assert "ORM insert" in failing and "raw SQL" in failing


def test_audit_fails_if_the_external_action_guard_is_broken(seeded, monkeypatch):
    import integrations.whatsapp_service as wa

    monkeypatch.setattr(wa, "demo_mode_enabled", lambda: False)  # simulate adapters that ignore demo mode
    monkeypatch.setattr(wa.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("blocked in test")))
    results, _ = _run(seeded)
    ok, _ = summarize(results)
    assert not ok


def test_audit_refuses_to_attack_a_database_that_looks_like_production(seeded):
    with allow_demo_writes(), Session(seeded) as session:
        organization_service.create_organization(session, name="Real Clinic", slug="real-clinic")
        session.commit()
    results, output = _run(seeded)
    names = " | ".join(r.name for r in results)
    assert "audit aborted before any attack" in names
    assert "ORM insert" not in names and "raw SQL" not in names  # not a single attack was attempted
    ok, _ = summarize(results)
    assert not ok


def test_audit_refuses_an_unseeded_database(tmp_path, monkeypatch):
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tripwire, "environment_problems", lambda env=None: [])
    results, _ = _run(engine)
    assert any(r.name == "audit aborted before any attack" for r in results)
    engine.dispose()


def test_audit_cli_applies_the_same_target_safety_as_seed_and_reset(seeded, monkeypatch, tmp_path):
    from demo import audit_cli
    from demo.operator_cli import TARGET_ENV

    def pg(user, pw, host):
        return "postgresql://" + user + ":" + pw + "@" + host + "/demo_db"

    lines: list[str] = []
    assert audit_cli.main([], {TARGET_ENV: pg("u", "SECRET-SENTINEL-AUDIT", "db.demo-host.example"),
                               "WHATSAPP_ACCESS_TOKEN": "x"}, out=lines.append, root=tmp_path) == 2
    assert "REFUSING" in " ".join(lines) and "SECRET-SENTINEL-AUDIT" not in " ".join(lines)

    lines.clear()
    code = audit_cli.main(
        [], {TARGET_ENV: pg("u", "SECRET-SENTINEL-AUDIT", "db.demo-host.example")}, out=lines.append,
        engine_factory=lambda url: seeded, set_environment=lambda url: None,
        migration_state=lambda e: ("head", "head"), root=tmp_path,
    )
    output = "\n".join(lines)
    assert code == 0, output
    assert "DEPLOYMENT AUDIT: PASS" in output and "SECRET-SENTINEL-AUDIT" not in output


def test_audit_cli_refuses_a_checkout_with_a_dotenv(seeded, tmp_path):
    from demo import audit_cli
    from demo.operator_cli import TARGET_ENV

    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    lines: list[str] = []
    env = {TARGET_ENV: "postgresql://" + "u" + ":" + "pw" + "@" + "db.demo-host.example" + "/demo_db"}
    assert audit_cli.main([], env, out=lines.append, root=tmp_path, engine_factory=lambda url: seeded,
                          set_environment=lambda url: None) == 2
    assert "git worktree add" in " ".join(lines)
