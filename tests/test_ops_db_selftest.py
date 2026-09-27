"""core/ops_db_selftest.py — read-only jarvis_learning_records/enum inspection.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)


def test_sqlite_reports_not_postgres_and_touches_nothing(monkeypatch, tmp_path):
    from core.db.session import make_engine
    from core.db.base import Base
    import core.db.models  # noqa: F401

    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("core.db.session.make_engine", lambda: engine)

    from core.ops_db_selftest import run_db_selftest

    result = run_db_selftest()
    assert result.dialect == "sqlite"
    assert result.table_exists is None
    assert "Not Postgres" in result.detail
    engine.dispose()


def test_engine_creation_failure_is_reported_not_raised(monkeypatch):
    def _boom():
        raise RuntimeError("no DATABASE_URL")

    monkeypatch.setattr("core.db.session.make_engine", _boom)

    from core.ops_db_selftest import run_db_selftest

    result = run_db_selftest()
    assert "Could not create a database engine" in result.detail
    assert "RuntimeError" in result.detail


def test_diagnostics_button_present_only_when_enabled(monkeypatch):
    from core.ops_diagnostics import diagnostics_enabled

    monkeypatch.delenv("LEADLENS_OPS_DIAGNOSTICS", raising=False)
    assert not diagnostics_enabled()
