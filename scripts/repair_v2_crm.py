"""Explicit repair/resynchronization utility for the Phase 3 CRM
relational shadow store.

NOT run automatically by anything. This is the tool operators use when
services/relational_sync_service.py's live dual-write hook recorded a
ShadowSyncFailure (a legacy CRM write succeeded but its relational
shadow write did not — see docs/V2_PHASE3_CRM_DUAL_WRITE.md) —
re-attempts the sync from the current legacy record (the authoritative
source of truth) and, on success, marks the matching unresolved
ShadowSyncFailure row(s) resolved.

Idempotent: re-running against an already-synced record just confirms
it, generating no failure and no duplicate relational row (sync_one()
upserts by external_id, same as the live hook and the backfill script).

Usage (from the repo root, with the venv active):

    python scripts/repair_v2_crm.py --entity leads --record-id L-004
    python scripts/repair_v2_crm.py --entity leads --all
    python scripts/repair_v2_crm.py --all
    python scripts/repair_v2_crm.py --verify
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.db.models.shadow_sync import ShadowSyncFailure  # noqa: E402
from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402
from core.identity.default_organization import (  # noqa: E402
    DEFAULT_ORGANIZATION_NAME,
    DEFAULT_ORGANIZATION_SLUG,
)
from core.identity.organization_service import create_organization, get_organization_by_slug  # noqa: E402
from services import clinic_data_service  # noqa: E402
from services.relational_sync_service import (  # noqa: E402
    ENTITY_SYNC_ORDER,
    LEGACY_ID_FIELD,
    _classify_error,
    record_sync_failure,
    sync_one,
)


def _resolve_matching_failures(session, org_id: int, entity: str, external_id: str) -> None:
    now = datetime.now(timezone.utc)
    (
        session.query(ShadowSyncFailure)
        .filter(
            ShadowSyncFailure.organization_id == org_id,
            ShadowSyncFailure.entity == entity,
            ShadowSyncFailure.external_id == external_id,
            ShadowSyncFailure.resolved.is_(False),
        )
        .update({"resolved": True, "resolved_at": now}, synchronize_session=False)
    )


def repair_record(
    *, org_slug: str = DEFAULT_ORGANIZATION_SLUG, org_name: str = DEFAULT_ORGANIZATION_NAME,
    entity: str, record_id: str, engine=None,
) -> bool:
    """Returns True on success. Raises nothing — failures are recorded
    exactly like the live dual-write hook."""
    owns_engine = engine is None
    engine = engine or make_engine()
    try:
        legacy_row = clinic_data_service.get_record(entity, record_id, include_archived=True)
        if legacy_row is None:
            print(f"  {entity} {record_id}: not found in legacy store — nothing to repair.")
            return False
        with session_scope(engine) as session:
            org = get_organization_by_slug(session, org_slug)
            org_id = org.id if org is not None else create_organization(session, name=org_name, slug=org_slug).id
            try:
                sync_one(session, org_id, entity, legacy_row)
                _resolve_matching_failures(session, org_id, entity, record_id)
                print(f"  {entity} {record_id}: repaired.")
                return True
            except Exception as exc:  # noqa: BLE001
                category, summary = _classify_error(exc)
                record_sync_failure(
                    session, organization_id=org_id, entity=entity, external_id=record_id,
                    operation="repair", error_category=category, error_summary=summary,
                )
                print(f"  {entity} {record_id}: still failing ({category}: {summary}).")
                return False
    finally:
        if owns_engine:
            engine.dispose()


def repair_entity(
    *, org_slug: str = DEFAULT_ORGANIZATION_SLUG, org_name: str = DEFAULT_ORGANIZATION_NAME,
    entity: str, engine=None,
) -> dict[str, int]:
    owns_engine = engine is None
    engine = engine or make_engine()
    id_field = LEGACY_ID_FIELD[entity]
    repaired = 0
    failed = 0
    try:
        rows = clinic_data_service.list_records(entity, include_archived=True)
        with session_scope(engine) as session:
            org = get_organization_by_slug(session, org_slug)
            org_id = org.id if org is not None else create_organization(session, name=org_name, slug=org_slug).id
            for row in rows:
                record_id = str(row.get(id_field, "")).strip()
                if not record_id:
                    continue
                try:
                    sync_one(session, org_id, entity, row)
                    _resolve_matching_failures(session, org_id, entity, record_id)
                    repaired += 1
                except Exception as exc:  # noqa: BLE001
                    category, summary = _classify_error(exc)
                    record_sync_failure(
                        session, organization_id=org_id, entity=entity, external_id=record_id,
                        operation="repair", error_category=category, error_summary=summary,
                    )
                    failed += 1
        print(f"{entity}: {repaired} repaired, {failed} still failing (of {len(rows)} legacy rows)")
    finally:
        if owns_engine:
            engine.dispose()
    return {"repaired": repaired, "failed": failed, "legacy_rows": len(rows)}


def verify_unresolved(*, org_slug: str = DEFAULT_ORGANIZATION_SLUG, engine=None) -> int:
    """Prints and returns the count of currently-unresolved shadow-sync
    failures for the target organization."""
    owns_engine = engine is None
    engine = engine or make_engine()
    try:
        with session_scope(engine) as session:
            org = get_organization_by_slug(session, org_slug)
            if org is None:
                print("No organization found — 0 unresolved failures.")
                return 0
            rows = (
                session.query(ShadowSyncFailure)
                .filter(ShadowSyncFailure.organization_id == org.id, ShadowSyncFailure.resolved.is_(False))
                .all()
            )
            by_entity: dict[str, int] = {}
            for row in rows:
                by_entity[row.entity] = by_entity.get(row.entity, 0) + 1
            if not rows:
                print("0 unresolved shadow-sync failures.")
            else:
                print(f"{len(rows)} unresolved shadow-sync failures:")
                for entity, count in sorted(by_entity.items()):
                    print(f"  {entity}: {count}")
            return len(rows)
    finally:
        if owns_engine:
            engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", dest="org_slug", default=DEFAULT_ORGANIZATION_SLUG)
    parser.add_argument("--organization-name", dest="org_name", default=DEFAULT_ORGANIZATION_NAME)
    parser.add_argument("--entity", choices=ENTITY_SYNC_ORDER)
    parser.add_argument("--record-id")
    parser.add_argument("--all", action="store_true", help="Repair every record (of --entity, or every entity if omitted).")
    parser.add_argument("--verify", action="store_true", help="Report unresolved failure count only; repairs nothing.")
    args = parser.parse_args()

    print(f"Target database: {get_database_url()}")

    if args.verify:
        count = verify_unresolved(org_slug=args.org_slug)
        return 1 if count else 0

    if args.record_id:
        if not args.entity:
            parser.error("--record-id requires --entity")
        ok = repair_record(org_slug=args.org_slug, org_name=args.org_name, entity=args.entity, record_id=args.record_id)
        return 0 if ok else 1

    if args.all:
        entities = (args.entity,) if args.entity else ENTITY_SYNC_ORDER
        total_failed = 0
        for entity in entities:
            result = repair_entity(org_slug=args.org_slug, org_name=args.org_name, entity=entity)
            total_failed += result["failed"]
        return 1 if total_failed else 0

    parser.error("specify --record-id (with --entity), --all, or --verify")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
