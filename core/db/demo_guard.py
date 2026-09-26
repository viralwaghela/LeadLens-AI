"""Public-demo mode: the database-level write guard.

A single SQLAlchemy `before_cursor_execute` listener on the Engine class,
so it sits BELOW the ORM and sees every statement any engine sends — ORM
flushes, Core inserts/updates/deletes, `session.execute(text("INSERT ..."))`,
DDL (`create_all`, `DROP`, `ALTER`). There is no separate per-module write
gate to forget: a new service that writes through SQLAlchemy is covered the
moment it exists.

Behavior:

    LEADLENS_DEMO_MODE unset   -> returns immediately. Zero effect on any
                                  normal deployment (one env-var read per
                                  statement).
    LEADLENS_DEMO_MODE set     -> any INSERT/UPDATE/DELETE/REPLACE/DDL
                                  statement raises core.demo_mode.DemoModeError
                                  unless (a) it is inside
                                  `allow_demo_writes()` (operator scripts and
                                  Alembic only), or (b) it targets the
                                  allowlisted usage-counter table, which the
                                  LLM limiter must write to enforce its
                                  global caps.

SELECTs, transaction control (BEGIN/COMMIT/ROLLBACK/SAVEPOINT) and
`SELECT ... FOR UPDATE` are untouched.

This does not cover core/memory.py's legacy store, which uses the DB-API
directly rather than SQLAlchemy — that store is guarded separately at its
low-level write executors (see core/memory.py).
"""
from __future__ import annotations

import re

from sqlalchemy import event
from sqlalchemy.engine import Engine

from core.demo_mode import DemoModeError, demo_mode_enabled, writes_allowed

# The only table the running demo app may write to.
ALLOWED_WRITE_TABLES = frozenset({"demo_usage"})

_DML_RE = re.compile(
    r"""^\s*(?:
            INSERT\s+(?:OR\s+\w+\s+)?INTO
          | REPLACE\s+INTO
          | UPDATE(?:\s+ONLY)?
          | DELETE\s+FROM(?:\s+ONLY)?
        )\s+["`\[]?(?P<table>[\w.]+)""",
    re.IGNORECASE | re.VERBOSE,
)
_DDL_RE = re.compile(
    r"^\s*(?:CREATE|DROP|ALTER|TRUNCATE|MERGE|COPY|GRANT|REVOKE|REINDEX|VACUUM)\b",
    re.IGNORECASE,
)
_CTE_WRITE_RE = re.compile(r"^\s*WITH\b.*\b(?:INSERT|UPDATE|DELETE)\b", re.IGNORECASE | re.DOTALL)


def _table_of(statement: str) -> str | None:
    match = _DML_RE.match(statement)
    if not match:
        return None
    return match.group("table").split(".")[-1].strip('"`[]').lower()


def statement_is_blocked(statement: str) -> bool:
    """Pure classifier (unit-testable without an engine): would this
    statement be refused in demo mode outside an allow_demo_writes()
    block?"""
    table = _table_of(statement)
    if table is not None:
        return table not in ALLOWED_WRITE_TABLES
    if _DDL_RE.match(statement):
        return True
    if _CTE_WRITE_RE.match(statement):
        return True  # a data-modifying CTE: refuse, we can't cheaply tell the table
    return False


@event.listens_for(Engine, "before_cursor_execute")
def _demo_statement_guard(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    if not demo_mode_enabled() or writes_allowed():
        return
    if statement_is_blocked(statement):
        raise DemoModeError("database write")
