"""Alembic environment for LeadLens CareOS's V2 relational schema.

Manual/CLI use only. Nothing in the live app (app.py, dashboard.py,
services/, ui/, scheduler/) imports this file or triggers it — Alembic
only ever runs when a human explicitly types `alembic upgrade head` (or
similar) at a terminal. There is no automatic-migration-at-startup
anywhere in this repository, and Phase 0 does not add one.

Connects using the exact same DATABASE_URL env var core/memory.py reads
(core.db.session.get_database_url()), so running this against a real
deployment's Postgres applies Phase 0's new tables to the SAME database
memory_store already lives in — that's the intended coexistence design,
not an accident. It only ever runs CREATE TABLE-shaped migrations in
Phase 0; nothing here touches memory_store.

`load_dotenv()` is called below so a local `alembic upgrade head` run
picks up whatever DATABASE_URL is in .env, same as any other manual
script in this repo. Be deliberate about what .env points at before
running Alembic commands — see docs/V2_COEXISTENCE.md.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv()

import core.db.models  # noqa: E402,F401 (populates Base.metadata as a side effect)
from core.db.base import Base  # noqa: E402
from core.db.session import get_database_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override whatever alembic.ini's sqlalchemy.url placeholder says with the
# same DATABASE_URL resolution core/memory.py and core/db/session.py use.
config.set_main_option("sqlalchemy.url", get_database_url())


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
