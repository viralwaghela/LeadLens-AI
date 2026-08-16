"""Phase 9 — production backup script.

Two data stores may need backing up, depending on the deployment:

    1. The relational V2 schema (core/db/) — Postgres: `pg_dump` (custom
       format, -Fc, so `pg_restore` can do selective/parallel restores).
       Local SQLite: sqlite3's own online backup API (safe to call while
       the app is running — takes a consistent snapshot even mid-write).
    2. The legacy memory_store (core/memory.py) — reuses
       `core.memory.backup_now()`, which already exists and already
       handles both backends correctly (a true binary SQLite copy, or a
       JSON export on Postgres) — not reimplemented here.

On Postgres, both #1 and #2 live in the SAME database, so one pg_dump
covers both; #2's backup_now() is only additionally invoked for the
local-SQLite case, where they are two separate files.

Never logs DATABASE_URL, a password, or any other secret — every log
line is scrubbed. This script does not manage where you store the
backup file afterward (see docs/BACKUP_RESTORE.md for retention
guidance) — encrypt/transfer it according to your hosting provider's
own security practices.

Usage:

    python scripts/backup_database.py --out backups/
    python scripts/backup_database.py --out backups/ --label pre-deploy
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

_DSN_PATTERN = re.compile(r"(postgres(?:ql)?://)[^@]*@", re.IGNORECASE)


def _scrub(text: str) -> str:
    """Removes credentials from a connection string before it's ever
    printed — scheme://user:password@host becomes scheme://<redacted>@host."""
    return _DSN_PATTERN.sub(r"\1<redacted>@", text)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_postgres_relational(database_url: str, out_dir: Path, label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"leadlens_{label}_{_timestamp()}.dump"
    print(f"Backing up Postgres database (pg_dump, custom format) to {destination} ...")
    result = subprocess.run(
        ["pg_dump", "-Fc", "-f", str(destination), database_url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {_scrub(result.stderr)}")
    print(f"Backup complete: {destination} ({destination.stat().st_size} bytes)")
    return destination


def backup_sqlite_relational(db_path: Path, out_dir: Path, label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        raise FileNotFoundError(f"No local relational database file found at {db_path}")
    destination = out_dir / f"leadlens_{label}_relational_{_timestamp()}.db"
    print(f"Backing up local relational SQLite database (online backup API) to {destination} ...")
    source_conn = sqlite3.connect(str(db_path))
    dest_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()
    print(f"Backup complete: {destination} ({destination.stat().st_size} bytes)")
    return destination


def run_backup(out_dir: Path, label: str) -> list[Path]:
    """Returns every file actually backed up."""
    from core.db.session import DEFAULT_SQLITE_PATH, get_database_url

    database_url = get_database_url()
    backed_up: list[Path] = []

    if database_url.startswith("postgres"):
        # One dump covers both the relational schema and the legacy
        # memory_store table — they're in the same database.
        backed_up.append(backup_postgres_relational(database_url, out_dir, label))
        return backed_up

    backed_up.append(backup_sqlite_relational(DEFAULT_SQLITE_PATH, out_dir, label))

    try:
        import core.memory as business_memory

        out_dir.mkdir(parents=True, exist_ok=True)
        legacy_destination = out_dir / f"leadlens_{label}_legacy_{_timestamp()}.db"
        business_memory.backup_now(legacy_destination)
        print(f"Legacy memory_store backup complete: {legacy_destination}")
        backed_up.append(legacy_destination)
    except Exception as exc:  # noqa: BLE001 - the relational backup above must not be lost over this
        print(f"WARNING: could not back up legacy memory_store file: {_scrub(str(exc))}")

    return backed_up


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="Directory to write the backup file(s) into.")
    parser.add_argument("--label", default="manual", help="Short label included in the filename (e.g. 'pre-deploy').")
    args = parser.parse_args()

    try:
        files = run_backup(Path(args.out), args.label)
    except Exception as exc:  # noqa: BLE001
        print(f"BACKUP FAILED: {_scrub(str(exc))}", file=sys.stderr)
        return 1
    print(f"\n{len(files)} backup file(s) written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
