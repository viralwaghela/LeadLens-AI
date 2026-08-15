"""Explicit, manual, idempotent migration of Jarvis's learning memory from
the legacy JSON file into the Phase 2 durable database store.

NOT run automatically by anything. Not imported by app.py, dashboard.py,
or services/jarvis_memory.py itself — the only way this ever executes is
a human typing one of the commands below at a terminal. This is
deliberate: services/jarvis_memory.py's DB path already works correctly
without this script ever running (it just falls back to reading the
legacy JSON file until migrated data exists — see that module's
docstring and docs/V2_PHASE2_JARVIS_MEMORY.md). This script exists to
move existing history into the database sooner, and to prove the two
stores agree.

What it does, using whatever DATABASE_URL/LEADLENS_V2_DATABASE_URL the
environment currently has (same resolution as jarvis_memory.py itself):

    1. reads and validates data/learning/learning_memory.json (or
       --json-path)
    2. get-or-creates the target Organization by slug (defaults to the
       same DEFAULT_ORGANIZATION_SLUG jarvis_memory.py itself resolves
       to, so a plain `migrate` run lines up with what the live app
       already reads/writes)
    3. for every preference/recommendation/outcome/execution row,
       inserts a JarvisLearningRecord IF AND ONLY IF no row with that
       (organization, record_type, fingerprint) already exists —
       existing DB rows (which may be newer than the JSON snapshot, e.g.
       written by the live app after Phase 2 deployed but before this
       script ran) are never overwritten, only reported as
       "already_present"
    4. never touches or deletes the legacy JSON file
    5. is safe to rerun any number of times — a second run reports
       everything as "already_present" and creates nothing new

Usage (from the repo root, with the venv active):

    python scripts/migrate_jarvis_memory_to_db.py --dry-run
    python scripts/migrate_jarvis_memory_to_db.py
    python scripts/migrate_jarvis_memory_to_db.py --verify

--verify runs no writes at all — it compares the legacy JSON file
against the current DB contents (per-type counts, plus a payload-hash
comparison for every row present in both) and reports mismatches
clearly. Run it after a plain migration, or any time, to sanity-check
the two stores agree. See docs/V2_PHASE2_JARVIS_MEMORY.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.db.models.jarvis import JarvisLearningRecord, JarvisLearningRecordType  # noqa: E402
from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402
from core.identity.organization_service import create_organization, get_organization_by_slug  # noqa: E402
from services.jarvis_memory import (  # noqa: E402
    DEFAULT_ORGANIZATION_NAME,
    DEFAULT_ORGANIZATION_SLUG,
    STORE as DEFAULT_STORE,
    _migrate as normalize_legacy_memory,
    _row_fingerprint,
)

_RECORD_TYPE_BY_KEY = {
    "preferences": JarvisLearningRecordType.PREFERENCE,
    "recommendations": JarvisLearningRecordType.RECOMMENDATION,
    "outcomes": JarvisLearningRecordType.OUTCOME,
    "executions": JarvisLearningRecordType.EXECUTION,
}
# Which field on each row type carries its original timestamp, so
# migrated rows keep their real history instead of all showing "migrated
# just now".
_TIMESTAMP_FIELD_BY_KEY = {
    "preferences": "created_at",
    "recommendations": "created_at",
    "outcomes": "recorded_at",
    "executions": "recorded_at",
}


def _load_legacy_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Returns (normalized_data, None) on success, or (None, error) if
    the file is missing/malformed/not a JSON object. Deliberately
    stricter at this top level than jarvis_memory.py's own tolerant
    loader (which silently treats a malformed file as "empty" for live
    read safety) — a migration should surface corruption, not paper
    over it."""
    if not path.exists():
        return None, f"file not found: {path}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"could not read file: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON: {exc}"
    if not isinstance(raw, dict):
        return None, "expected a JSON object at the top level"
    return normalize_legacy_memory(raw), None


def _parse_original_timestamp(row: dict[str, Any], key: str) -> datetime:
    field = _TIMESTAMP_FIELD_BY_KEY[key]
    raw = row.get(field)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = datetime.fromisoformat(raw.strip())
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _payload_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def migrate(
    *,
    json_path: Path = DEFAULT_STORE,
    org_slug: str = DEFAULT_ORGANIZATION_SLUG,
    org_name: str = DEFAULT_ORGANIZATION_NAME,
    dry_run: bool = False,
    engine=None,
) -> dict[str, Any]:
    """`engine`: pass an existing engine to reuse it (and skip
    disposal) — used by tests to inject an isolated in-memory database.
    Defaults to None, which creates one via make_engine() and disposes
    it when done, exactly like the CLI's normal behavior."""
    data, error = _load_legacy_json(json_path)
    report: dict[str, Any] = {"json_path": str(json_path), "error": error, "by_type": {}}
    if error is not None:
        print(f"Cannot migrate: {error}")
        return report

    owns_engine = engine is None
    engine = engine or make_engine()
    try:
        with session_scope(engine) as session:
            org = get_organization_by_slug(session, org_slug)
            if org is None:
                if dry_run:
                    print(f"[dry-run] Would create Organization(slug={org_slug!r}, name={org_name!r}).")
                    org_id = None
                else:
                    org = create_organization(session, name=org_name, slug=org_slug)
                    org_id = org.id
                    print(f"Created Organization(id={org.id}, slug={org.slug!r}).")
            else:
                org_id = org.id
                print(f"Using existing Organization(id={org.id}, slug={org.slug!r}).")

            for key, record_type in _RECORD_TYPE_BY_KEY.items():
                rows = data.get(key, [])
                created = 0
                already_present = 0
                skipped_invalid = 0
                for row in rows:
                    if not isinstance(row, dict):
                        skipped_invalid += 1
                        continue
                    fingerprint = _row_fingerprint(row)
                    if org_id is not None:
                        existing = (
                            session.query(JarvisLearningRecord)
                            .filter(
                                JarvisLearningRecord.organization_id == org_id,
                                JarvisLearningRecord.record_type == record_type,
                                JarvisLearningRecord.fingerprint == fingerprint,
                            )
                            .one_or_none()
                        )
                    else:
                        existing = None  # dry-run with no org yet: nothing can exist
                    if existing is not None:
                        already_present += 1
                        continue
                    if dry_run:
                        created += 1  # "would create"
                        continue
                    timestamp = _parse_original_timestamp(row, key)
                    session.add(
                        JarvisLearningRecord(
                            organization_id=org_id,
                            record_type=record_type,
                            external_id=str(row.get("id") or "")[:60] or None,
                            fingerprint=fingerprint,
                            payload=json.dumps(row, ensure_ascii=False, default=str),
                            created_at=timestamp,
                            updated_at=timestamp,
                        )
                    )
                    created += 1
                report["by_type"][key] = {
                    "json_rows": len(rows),
                    "created": created,
                    "already_present": already_present,
                    "skipped_invalid": skipped_invalid,
                }
    finally:
        if owns_engine:
            engine.dispose()

    verb = "Would migrate" if dry_run else "Migrated"
    for key, counts in report["by_type"].items():
        print(
            f"{verb} {key}: {counts['created']} new, "
            f"{counts['already_present']} already present, "
            f"{counts['skipped_invalid']} skipped (invalid row)"
        )
    return report


def verify(
    *,
    json_path: Path = DEFAULT_STORE,
    org_slug: str = DEFAULT_ORGANIZATION_SLUG,
    engine=None,
) -> dict[str, Any]:
    """`engine`: see migrate()'s docstring — same injectable-engine
    pattern for tests."""
    data, error = _load_legacy_json(json_path)
    report: dict[str, Any] = {"json_path": str(json_path), "error": error, "by_type": {}, "ok": False}
    if error is not None:
        print(f"Cannot verify: {error}")
        return report

    owns_engine = engine is None
    engine = engine or make_engine()
    try:
        with session_scope(engine) as session:
            org = get_organization_by_slug(session, org_slug)
            org_id = org.id if org is not None else None

            overall_ok = True
            for key, record_type in _RECORD_TYPE_BY_KEY.items():
                json_rows = [row for row in data.get(key, []) if isinstance(row, dict)]
                db_rows = (
                    session.query(JarvisLearningRecord)
                    .filter(
                        JarvisLearningRecord.organization_id == org_id,
                        JarvisLearningRecord.record_type == record_type,
                    )
                    .all()
                    if org_id is not None
                    else []
                )
                db_by_fingerprint = {row.fingerprint: row for row in db_rows}

                missing_from_db: list[str] = []
                hash_mismatches: list[str] = []
                for row in json_rows:
                    fingerprint = _row_fingerprint(row)
                    db_row = db_by_fingerprint.get(fingerprint)
                    if db_row is None:
                        missing_from_db.append(fingerprint)
                        continue
                    if _payload_hash(json.loads(db_row.payload)) != _payload_hash(row):
                        hash_mismatches.append(fingerprint)

                type_ok = not missing_from_db and not hash_mismatches
                overall_ok = overall_ok and type_ok
                report["by_type"][key] = {
                    "json_count": len(json_rows),
                    "db_count": len(db_rows),
                    "missing_from_db": missing_from_db,
                    "hash_mismatches": hash_mismatches,
                    "ok": type_ok,
                }
                status = "OK" if type_ok else "MISMATCH"
                print(
                    f"{key}: json={len(json_rows)} db={len(db_rows)} "
                    f"missing_from_db={len(missing_from_db)} "
                    f"hash_mismatches={len(hash_mismatches)} [{status}]"
                )
            report["ok"] = overall_ok
    finally:
        if owns_engine:
            engine.dispose()

    print("VERIFY RESULT:", "PASS" if report["ok"] else "MISMATCH")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--org-slug", default=DEFAULT_ORGANIZATION_SLUG)
    parser.add_argument("--org-name", default=DEFAULT_ORGANIZATION_NAME)
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen; write nothing.")
    parser.add_argument(
        "--verify", action="store_true",
        help="Compare the legacy JSON file against current DB contents; writes nothing.",
    )
    args = parser.parse_args()

    print(f"Target database: {get_database_url()}")

    if args.verify:
        report = verify(json_path=args.json_path, org_slug=args.org_slug)
        return 0 if report["ok"] else 1

    report = migrate(
        json_path=args.json_path,
        org_slug=args.org_slug,
        org_name=args.org_name,
        dry_run=args.dry_run,
    )
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
