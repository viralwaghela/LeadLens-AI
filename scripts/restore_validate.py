"""Phase 9 — restore validation.

A backup is not proven good until a restore has actually been tested.
This script validates a **local SQLite relational backup** (the file
scripts/backup_database.py's `backup_sqlite_relational()` produces) by
restoring it into a brand-new, isolated copy and running the same
read-only checks scripts/production_readiness.py runs — schema/migration
compatibility, tenant integrity, cross-org FK sanity — against the
restored copy. Never touches the original backup file (opened read-only)
or any live database.

For a Postgres backup (pg_dump custom-format .dump file), restore into
an isolated, throwaway Postgres database first —

    createdb leadlens_restore_test
    pg_restore -d leadlens_restore_test path/to/backup.dump

— then point this script at that isolated database via --database-url;
this script never runs pg_restore itself (that requires a real Postgres
server, which isn't something to spin up implicitly from a validation
script — see docs/BACKUP_RESTORE.md for the full procedure).

Usage:

    python scripts/restore_validate.py --backup backups/leadlens_x_relational_....db
    python scripts/restore_validate.py --database-url postgresql://.../leadlens_restore_test
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()


def validate_restored_database(database_url: str) -> tuple[str, list[str]]:
    """Runs the same checks scripts/production_readiness.py's
    migration_drift + tenant_integrity sections run, against whichever
    database `database_url` points at. Returns (overall_level, lines)."""
    import core.db.session as db_session_mod

    original_get_url = db_session_mod.get_database_url
    db_session_mod.get_database_url = lambda: database_url
    try:
        from scripts.production_readiness import _section_migration_drift, _section_tenant_integrity

        lines: list[str] = []
        levels: list[str] = []

        level, section_lines = _section_migration_drift()
        levels.append(level)
        lines.append(f"[migration_drift: {level}]")
        lines.extend(section_lines)

        level, section_lines = _section_tenant_integrity()
        levels.append(level)
        lines.append(f"[tenant_integrity: {level}]")
        lines.extend(section_lines)

        overall = "FAIL" if "FAIL" in levels else ("WARN" if "WARN" in levels else "PASS")
        return overall, lines
    finally:
        db_session_mod.get_database_url = original_get_url


def restore_and_validate_sqlite(backup_path: Path) -> tuple[str, list[str]]:
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    with tempfile.TemporaryDirectory() as tmp:
        restored_path = Path(tmp) / "restored.db"
        shutil.copy2(backup_path, restored_path)  # never mutate the original backup
        return validate_restored_database(f"sqlite:///{restored_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", help="Path to a local SQLite relational backup file to restore and validate.")
    group.add_argument("--database-url", help="An already-restored, ISOLATED database to validate directly (e.g. after a manual pg_restore).")
    args = parser.parse_args()

    if args.backup:
        overall, lines = restore_and_validate_sqlite(Path(args.backup))
    else:
        overall, lines = validate_restored_database(args.database_url)

    print(f"Restore validation: {overall}\n")
    for line in lines:
        print(line)

    return 1 if overall == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
