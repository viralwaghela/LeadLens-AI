"""Engine/session handling for the V2 relational schema.

Deliberately mirrors core/memory.py's own posture: reads DATABASE_URL from
the environment but never calls load_dotenv() itself (that stays the
entry point's job — app.py never imports this module at all right now,
so there is no entry point for it yet). Nothing here runs at import time
beyond defining functions; no engine is created and no connection is
opened just by importing this module or core/db/models/.

Local/dev fallback: when DATABASE_URL is unset, this uses a SEPARATE
SQLite file (database/leadlens_v2.db) from core/memory.py's own
leadlens.db, on purpose — Phase 0's tables are not live-wired to
anything yet, and keeping them in a distinct local file avoids any
raw-sqlite3-vs-SQLAlchemy cross-access ambiguity while that's true. In
production (DATABASE_URL set to real Postgres), Phase 0's tables live in
the SAME database as memory_store, as separate tables — that's the
actual "coexistence" this phase is building, and is a deliberate choice,
not an accident: see docs/V2_COEXISTENCE.md.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "database" / "leadlens_v2.db"


def get_database_url(default_sqlite_path: Path | None = None) -> str:
    """Resolve the SQLAlchemy connection URL for this process.

    Reads the same DATABASE_URL env var core/memory.py uses, so a real
    deployment's Postgres database is the same one Phase 0's tables will
    live in — no second production database. Falls back to a dedicated
    local SQLite file when unset (see module docstring for why it's a
    different file from core/memory.py's).
    """
    raw = os.getenv("DATABASE_URL", "").strip()
    if raw:
        # SQLAlchemy's psycopg2 dialect requires the "postgresql://"
        # scheme; some providers (old Heroku-style URLs) still hand out
        # "postgres://", which SQLAlchemy 2.x rejects outright. Normalize
        # rather than fail on an otherwise-valid connection string.
        if raw.startswith("postgres://"):
            raw = "postgresql://" + raw[len("postgres://"):]
        return raw

    path = default_sqlite_path or DEFAULT_SQLITE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def make_engine(database_url: str | None = None, **kwargs) -> Engine:
    """Create a new engine. Callers own its lifecycle (dispose it when done)
    — this module does not hold a module-level engine singleton, so tests
    and Alembic can each construct an isolated one without interfering
    with each other or with any future application engine."""
    url = database_url or get_database_url()
    connect_args = {}
    is_sqlite = url.startswith("sqlite:")
    if is_sqlite:
        # Needed because Streamlit/session usage crosses threads; Phase 0
        # itself is single-threaded (tests, scripts, Alembic), but this
        # keeps the engine usable the same way core/memory.py's own
        # sqlite3 connections are (check_same_thread=False there too).
        connect_args["check_same_thread"] = False

    engine = create_engine(url, connect_args=connect_args, **kwargs)

    if is_sqlite:
        # SQLite does NOT enforce foreign keys unless this pragma is set
        # on every connection — Postgres always enforces them, so without
        # this, the composite (organization_id, patient_id)-style FKs in
        # core/db/models/clinic.py (the ones that make a cross-tenant
        # reference impossible) would silently do nothing on the local/
        # test SQLite fallback while working correctly in production.
        # Found by test_composite_foreign_keys_enforce_same_organization
        # actually failing against a plain SQLite engine without this.
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One transaction per with-block: commits on clean exit, rolls back
    and re-raises on any exception. Callers pass in their own engine
    (from make_engine()) rather than this module holding global state."""
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
