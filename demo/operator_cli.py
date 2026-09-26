"""Operator command line for seeding / resetting the public demo tenant.

Used by scripts/seed_demo.py and scripts/reset_demo.py. Everything that can
go wrong when a human points a destructive command at a database is handled
here, BEFORE the seeder runs:

  * The target comes ONLY from LEADLENS_DEMO_DATABASE_URL — a dedicated
    variable. An ambient DATABASE_URL (or a .env file) is ignored and
    overridden, and .env is never loaded, so a shell that happens to hold the
    production URL cannot be targeted by accident.
  * The URL must be PostgreSQL (the demo uses its own persistent Postgres).
  * If any provider / production secret is present in the environment
    (WhatsApp, Gmail, Google, MASTER_*, CLIENT*_, the encryption key, ...) the
    command refuses to run — an operator shell that holds production
    credentials is not a demo shell.
  * The default is a DRY RUN. Writing requires --apply AND --confirm-host set
    to the target's actual host name, typed by hand.
  * The database is inspected first (read-only): migrations must be at head,
    and a database containing any non-demo organization — i.e. anything that
    looks like production — is refused by the seeder itself.
  * Output shows host and database name only, never credentials.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.demo_mode import DEMO_ORG_SLUG
from core.demo_tripwire import forbidden_env_variables

TARGET_ENV = "LEADLENS_DEMO_DATABASE_URL"


@dataclass(frozen=True)
class Target:
    host: str
    database: str


def parse_target(url: str) -> Target:
    """Validates the URL and returns host/database only (never credentials).
    Raises ValueError for anything that is not a PostgreSQL URL."""
    from sqlalchemy.engine import make_url

    if not url or not url.strip():
        raise ValueError(f"{TARGET_ENV} is not set")
    parsed = make_url(url.strip())
    if parsed.drivername != "postgresql":
        raise ValueError(
            "the demo database URL must be a plain postgresql:// URL (SQLite, other databases, and driver "
            "suffixes such as +psycopg are refused — the legacy store cannot parse those)"
        )
    if not parsed.host:
        raise ValueError("the demo database URL has no host")
    return Target(host=parsed.host, database=parsed.database or "")


REPO_ROOT = Path(__file__).resolve().parents[1]


def dotenv_problems(root: Path | None = None) -> list[str]:
    """A .env in this checkout may hold PRODUCTION values, and several app modules call
    dotenv auto-loading on import (services/ai.py, ...), which would quietly load them into an
    operator process. Refuse to run from such a checkout: use a clean git worktree."""
    if ((root or REPO_ROOT) / ".env").exists():
        return [
            "a .env file exists in this checkout (it may hold production values and app modules auto-load it). "
            "Run from a clean checkout: git worktree add ../LeadLens-demo-ops <demo-branch>"
        ]
    return []


def operator_environment_problems(env: Mapping[str, str]) -> list[str]:
    """Problems with the operator's shell — names only, never values."""
    problems: list[str] = []
    present = forbidden_env_variables(env)
    if present:
        problems.append("provider/production variable(s) present in this shell: " + ", ".join(present))
    try:
        parse_target(env.get(TARGET_ENV, ""))
    except ValueError as error:
        problems.append(str(error))
    return problems


def _migration_state(engine) -> tuple[str | None, str | None]:
    """(current revision in the database, head revision in the code)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect

    root = Path(__file__).resolve().parents[1]
    head = ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_current_head()
    if "alembic_version" not in inspect(engine).get_table_names():
        return None, head
    with engine.connect() as connection:
        current = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    return current, head


def _point_app_at(url: str) -> None:
    """Point every part of the app at the demo database and NOWHERE else.
    Deliberately overrides any ambient DATABASE_URL; .env is never loaded."""
    os.environ["DATABASE_URL"] = url
    os.environ["LEADLENS_V2_DATABASE_URL"] = url
    os.environ["LEADLENS_DEFAULT_ORG_SLUG"] = DEMO_ORG_SLUG


def main(
    mode: str,
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
    *,
    engine_factory: Callable[[str], object] | None = None,
    set_environment: Callable[[str], None] | None = None,
    seed_dir: Path | None = None,
    root: Path | None = None,
) -> int:
    """`engine_factory`, `set_environment` and `seed_dir` exist so tests can
    run the real flow against a throwaway database and directory."""
    if mode not in {"seed", "reset"}:
        raise ValueError(f"unknown mode {mode!r}")
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        description=f"{mode.capitalize()} the public demo tenant (dry run unless --apply).",
    )
    parser.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    parser.add_argument("--confirm-host", default="", help="type the target's host name to allow --apply")
    parser.add_argument("--anchor", default="", help="YYYY-MM-DD date the data is anchored to (default: today)")
    args = parser.parse_args(argv)

    problems = dotenv_problems(root) + operator_environment_problems(env)
    if problems:
        for problem in problems:
            out(f"REFUSING: {problem}")
        return 2
    target = parse_target(env[TARGET_ENV])
    url = env[TARGET_ENV].strip()

    try:
        anchor = date.fromisoformat(args.anchor) if args.anchor else None
    except ValueError:
        out("REFUSING: --anchor must be YYYY-MM-DD")
        return 2

    out(f"Target: host={target.host} database={target.database or '(default)'}")
    out(f"Mode:   {mode} ({'APPLY' if args.apply else 'DRY RUN'})")

    (set_environment or _point_app_at)(url)

    from core.db.session import make_engine

    engine = (engine_factory or make_engine)(url)
    owns_engine = engine_factory is None  # never dispose an engine the caller injected
    try:
        current, head = _migration_state(engine)
        if current != head:
            out(f"REFUSING: the demo database is at migration {current!r}, code expects {head!r}. "
                "Run `alembic upgrade head` against the demo database first (LEADLENS_V2_DATABASE_URL set to it).")
            return 2

        from sqlalchemy.orm import Session

        from core.demo_tripwire import database_problems
        from demo.seeder import DemoSeedError, _legacy_company, reset_demo, seed_demo

        with Session(engine) as session:
            db_problems = database_problems(session, _legacy_company())
        if db_problems:
            for problem in db_problems:
                out(f"REFUSING: {problem}")
            return 2

        if not args.apply:
            out(f"DRY RUN: would {mode} the demo tenant '{DEMO_ORG_SLUG}' on {target.host}. Nothing was written.")
            out("Re-run with --apply --confirm-host " + target.host + " to proceed.")
            return 0
        if args.confirm_host.strip().lower() != target.host.lower():
            out("REFUSING: --confirm-host must exactly match the target host name.")
            return 2

        try:
            options = {"anchor": anchor, **({"seed_dir": seed_dir} if seed_dir else {})}
            result = seed_demo(engine, **options) if mode == "seed" else reset_demo(engine, **options)
        except DemoSeedError as error:
            out(f"REFUSING: {error}")
            return 1
        out(f"Done: {result}")
        return 0
    finally:
        if owns_engine:
            engine.dispose()


if __name__ == "__main__":  # pragma: no cover - the scripts/ wrappers are the real entry points
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "seed", sys.argv[2:]))
