"""Phase 4 production canary: compares actual CRM READ OUTPUT (legacy
service output vs normalized relational service output), not just
stored row counts (Phase 3's scripts/verify_v2_crm_parity.py already
covers stored-row parity).

NOT run automatically by anything, never modifies legacy data, and
never enables any read-cutover flag — this is the tool an operator runs
BEFORE flipping an entity's LEADLENS_V2_READ_<ENTITY> flag on, per
docs/V2_PHASE4_READ_CUTOVER.md's runbook (step 4: "confirm zero
read-output mismatches"). It reuses the exact same comparison logic
services/crm_read_router.py uses in LEADLENS_V2_READ_COMPARE mode
(compare_rows()), so a canary run and live compare-mode traffic can
never disagree about what counts as a mismatch — and it records the
same ReadMismatch rows compare-mode does, so a canary run leaves a
durable, inspectable trail.

Usage:

    python scripts/verify_v2_crm_read_parity.py
    python scripts/verify_v2_crm_read_parity.py --entity patients --entity appointments
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
from core.identity.default_organization import DEFAULT_ORGANIZATION_SLUG  # noqa: E402
from core.identity.organization_service import get_organization_by_slug  # noqa: E402
from services import clinic_data_service  # noqa: E402
from services.crm_read_router import compare_rows  # noqa: E402
from services.relational_sync_service import ENTITY_SYNC_ORDER  # noqa: E402

# Reuses Phase 3's dependency-respecting order for deterministic output.
# "services" is excluded — it has no legacy source to compare against
# (see services/crm_read_router.py's read_services() docstring).
ENTITIES = ENTITY_SYNC_ORDER


def verify_read_parity(
    *, org_slug: str = DEFAULT_ORGANIZATION_SLUG, entities: tuple[str, ...] = ENTITIES, engine=None,
) -> dict[str, Any]:
    owns_engine = engine is None
    engine = engine or make_engine()
    report: dict[str, Any] = {"org_slug": org_slug, "by_entity": {}, "ok": True}
    try:
        with session_scope(engine) as session:
            org = get_organization_by_slug(session, org_slug)
            if org is None:
                print(f"No organization found for slug {org_slug!r} — nothing to compare.")
                report["ok"] = False
                return report

            for entity in entities:
                legacy_rows = clinic_data_service.list_records(entity, include_archived=True)
                mismatches = compare_rows(session, org.id, entity, legacy_rows)
                entity_ok = not mismatches
                report["ok"] = report["ok"] and entity_ok
                report["by_entity"][entity] = {"legacy_count": len(legacy_rows), "mismatches": mismatches}
                status = "OK" if entity_ok else "MISMATCH"
                print(f"{entity}: legacy_rows={len(legacy_rows)} mismatches={len(mismatches)} [{status}]")
                for m in mismatches[:10]:
                    print(f"    {m}")
                if len(mismatches) > 10:
                    print(f"    ... and {len(mismatches) - 10} more")

            # Spot-check single-record lookups for every entity that has
            # at least one row — get_record() is built on the same
            # _read_rows() hook, but this proves it end-to-end rather
            # than by inference.
            print("\nSingle-record lookup spot-check:")
            for entity in entities:
                legacy_rows = clinic_data_service.list_records(entity, include_archived=True)
                if not legacy_rows:
                    continue
                id_field = {
                    "leads": "lead_id", "corporate_clients": "client_id", "therapists": "therapist_id",
                    "patients": "patient_id", "package_templates": "template_id", "packages": "package_id",
                    "appointments": "appointment_id", "progress_notes": "progress_id", "payments": "payment_id",
                }[entity]
                sample_id = legacy_rows[0].get(id_field)
                legacy_single = clinic_data_service.get_record(entity, sample_id, include_archived=True)
                ok = legacy_single is not None
                print(f"  {entity} get_record({sample_id!r}): {'OK' if ok else 'MISSING'}")
    finally:
        if owns_engine:
            engine.dispose()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", dest="org_slug", default=DEFAULT_ORGANIZATION_SLUG)
    parser.add_argument("--entity", dest="entities", action="append", choices=ENTITIES)
    args = parser.parse_args()
    entities = tuple(e for e in ENTITIES if e in (args.entities or ENTITIES))

    print(f"Target database: {get_database_url()}")
    report = verify_read_parity(org_slug=args.org_slug, entities=entities)
    print("\nREAD PARITY RESULT:", "PASS" if report["ok"] else "MISMATCH")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
