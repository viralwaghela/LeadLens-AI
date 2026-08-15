"""Explicit, manual, idempotent backfill of existing legacy CRM data into
the Phase 3 relational shadow store.

NOT run automatically by anything. Not imported by app.py, dashboard.py,
or services/clinic_data_service.py — the only way this ever executes is
a human typing `python scripts/backfill_v2_crm.py` at a terminal. The
live dual-write hook (services/relational_sync_service.sync_upsert(),
called from clinic_data_service.py) already keeps NEW writes in sync
going forward; this script's purpose is moving EXISTING history in, and
is independent of whether dual-write is currently enabled.

Reads every legacy CRM entity via services/clinic_data_service.py (the
same read path the live app uses — never touches core/memory.py
directly), in dependency order (see
services.relational_sync_service.ENTITY_SYNC_ORDER — independent
entities first, then entities that reference them), and upserts each
row via services.relational_sync_service.sync_one() — the exact same
mapping/upsert logic the live dual-write hook uses, not a separate
implementation.

Idempotent and safe to rerun: sync_one() upserts by
(organization, entity, external_id) — an existing relational row is
updated in place to match the current legacy state, never duplicated,
and this script never deletes anything.

Does NOT hard-code "Beyond Pain" or any specific clinic — the target
organization is resolved from --organization (a slug), defaulting to
the same LEADLENS_DEFAULT_ORG_SLUG-driven default every other Phase 2/3
component uses, so a plain `backfill_v2_crm.py` run lines up with what
the live app already writes to.

Usage (from the repo root, with the venv active):

    python scripts/backfill_v2_crm.py --dry-run
    python scripts/backfill_v2_crm.py
    python scripts/backfill_v2_crm.py --organization my-clinic-slug
    python scripts/backfill_v2_crm.py --entity patients --entity appointments
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402
from core.identity.default_organization import (  # noqa: E402
    DEFAULT_ORGANIZATION_NAME,
    DEFAULT_ORGANIZATION_SLUG,
)
from core.identity.organization_service import create_organization, get_organization_by_slug  # noqa: E402
from services import clinic_data_service  # noqa: E402
from services.relational_sync_service import (  # noqa: E402
    ENTITY_SYNC_ORDER,
    _classify_error,
    record_sync_failure,
    sync_one,
)


def backfill(
    *,
    org_slug: str = DEFAULT_ORGANIZATION_SLUG,
    org_name: str = DEFAULT_ORGANIZATION_NAME,
    entities: tuple[str, ...] = ENTITY_SYNC_ORDER,
    dry_run: bool = False,
    engine=None,
) -> dict[str, Any]:
    """`engine`: pass an existing engine to reuse it (and skip
    disposal) — used by tests to inject an isolated database, same
    pattern as scripts/migrate_jarvis_memory_to_db.py."""
    report: dict[str, Any] = {"org_slug": org_slug, "by_entity": {}}

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

            for entity in entities:
                legacy_rows = clinic_data_service.list_records(entity, include_archived=True)
                created = 0
                updated = 0
                failed = 0
                for row in legacy_rows:
                    external_id = str(row.get(_id_field(entity), "")).strip()
                    if org_id is None:
                        # dry-run with no org yet: nothing can be checked against
                        # the DB, so just count every row as "would sync".
                        created += 1
                        continue
                    existed_before = _relational_row_exists(session, entity, org_id, external_id)
                    if dry_run:
                        if existed_before:
                            updated += 1
                        else:
                            created += 1
                        continue
                    try:
                        sync_one(session, org_id, entity, row)
                        if existed_before:
                            updated += 1
                        else:
                            created += 1
                    except Exception as exc:  # noqa: BLE001 - continue backfilling other rows
                        failed += 1
                        category, summary = _classify_error(exc)
                        record_sync_failure(
                            session,
                            organization_id=org_id,
                            entity=entity,
                            external_id=external_id or None,
                            operation="backfill",
                            error_category=category,
                            error_summary=summary,
                        )
                report["by_entity"][entity] = {
                    "legacy_rows": len(legacy_rows),
                    "created": created,
                    "updated": updated,
                    "failed": failed,
                }
                verb = "Would backfill" if dry_run else "Backfilled"
                print(
                    f"{verb} {entity}: {len(legacy_rows)} legacy rows -> "
                    f"{created} new, {updated} updated in place, {failed} failed"
                )
    finally:
        if owns_engine:
            engine.dispose()

    return report


def _id_field(entity: str) -> str:
    from services.relational_sync_service import LEGACY_ID_FIELD

    return LEGACY_ID_FIELD[entity]


def _relational_row_exists(session, entity: str, org_id: int, external_id: str) -> bool:
    from services.relational_sync_service import _SYNC_HANDLERS  # noqa: F401 - ensures entity is known
    from core.db.models import clinic as clinic_models

    model_by_entity = {
        "therapists": clinic_models.Therapist,
        "patients": clinic_models.Patient,
        "appointments": clinic_models.Appointment,
        "package_templates": clinic_models.PackageTemplate,
        "packages": clinic_models.Package,
        "payments": clinic_models.Payment,
        "progress_notes": clinic_models.ProgressNote,
        "leads": clinic_models.Lead,
        "corporate_clients": clinic_models.CorporateClient,
    }
    model = model_by_entity[entity]
    return (
        session.query(model)
        .filter(model.organization_id == org_id, model.external_id == external_id)
        .first()
        is not None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", dest="org_slug", default=DEFAULT_ORGANIZATION_SLUG)
    parser.add_argument("--organization-name", dest="org_name", default=DEFAULT_ORGANIZATION_NAME)
    parser.add_argument(
        "--entity", dest="entities", action="append", choices=ENTITY_SYNC_ORDER,
        help="Restrict to specific entities (repeatable). Defaults to all, in dependency order.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen; write nothing.")
    args = parser.parse_args()

    entities = tuple(args.entities) if args.entities else ENTITY_SYNC_ORDER
    # Preserve dependency order even if the user passed --entity out of order.
    entities = tuple(e for e in ENTITY_SYNC_ORDER if e in entities)

    print(f"Target database: {get_database_url()}")
    report = backfill(org_slug=args.org_slug, org_name=args.org_name, entities=entities, dry_run=args.dry_run)

    total_failed = sum(v["failed"] for v in report["by_entity"].values())
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
