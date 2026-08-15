"""Parity verification: compares legacy CRM data against the Phase 3
relational shadow store, entity by entity.

NOT run automatically by anything. Read-only — writes nothing to either
store. Intended to be run after scripts/backfill_v2_crm.py, and
periodically afterward (e.g. before enabling dual-write in production,
per docs/V2_PHASE3_CRM_DUAL_WRITE.md's deployment sequence), to prove
the two stores actually agree rather than assuming they do.

Checks, per entity:
    - counts (legacy vs relational)
    - business/external IDs present in one store but not the other
    - a small set of "important" fields per entity (status, key values,
      and — for payments/package_templates — the exact financial amount
      as Decimal, never float) for every record present in both stores
    - relationship IDs (e.g. an appointment's relational patient_id
      resolves back to the same legacy patient_id it was created with)

Report format:

    Patients
    legacy: 124
    relational: 124
    matched: 124
    mismatch: 0

Does not print row payloads (names, contact details, clinical notes) —
only counts, IDs, and the specific field values being compared, which
is already the level of detail an operator needs to diagnose a
mismatch without this report itself becoming a sensitive-data export.

Usage:

    python scripts/verify_v2_crm_parity.py
    python scripts/verify_v2_crm_parity.py --entity patients --entity appointments
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.db.models import clinic as clinic_models  # noqa: E402
from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402
from core.identity.default_organization import DEFAULT_ORGANIZATION_SLUG  # noqa: E402
from core.identity.organization_service import get_organization_by_slug  # noqa: E402
from services import clinic_data_service  # noqa: E402
from services.relational_sync_service import ENTITY_SYNC_ORDER, LEGACY_ID_FIELD  # noqa: E402

_MODEL_BY_ENTITY = {
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

# entity -> tuple of (legacy_field, relational_attr, comparator) checked
# for every record present in both stores. comparator is "text",
# "decimal", or "date" (all normalize before comparing so formatting
# differences alone don't count as a mismatch).
_IMPORTANT_FIELDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "therapists": (("name", "name", "text"), ("status", "status", "text")),
    "patients": (("name", "name", "text"), ("status", "status", "text")),
    "appointments": (("status", "status", "text"), ("appointment_date", "appointment_date", "date")),
    "package_templates": (("name", "name", "text"), ("status", "status", "text"), ("price", "price", "decimal")),
    "packages": (("status", "status", "text"), ("sessions_remaining", "sessions_remaining", "text")),
    "payments": (("status", "status", "text"), ("amount", "amount", "decimal")),
    "progress_notes": (("visit_date", "visit_date", "date"),),
    "leads": (("status", "status", "text"), ("source", "source", "text")),
    "corporate_clients": (("company_name", "company_name", "text"), ("status", "status", "text")),
}

# entity -> (legacy parent field, relational parent attr, parent entity)
_RELATIONSHIP_FIELDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "appointments": (("patient_id", "patient_id", "patients"), ("therapist_id", "therapist_id", "therapists")),
    "packages": (("patient_id", "patient_id", "patients"),),
    "payments": (("patient_id", "patient_id", "patients"), ("package_id", "package_id", "packages")),
    "progress_notes": (("patient_id", "patient_id", "patients"), ("therapist_id", "therapist_id", "therapists")),
}


def _normalize(value: Any, kind: str) -> Any:
    if kind == "decimal":
        try:
            return Decimal(str(value if value is not None else 0))
        except Exception:  # noqa: BLE001
            return None
    if kind == "date":
        text = str(value or "").strip()
        return text or None
    text = str(value if value is not None else "").strip()
    return text or None


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def verify_entity(session, org_id: int, entity: str) -> dict[str, Any]:
    id_field = LEGACY_ID_FIELD[entity]
    model = _MODEL_BY_ENTITY[entity]

    legacy_rows = clinic_data_service.list_records(entity, include_archived=True)
    legacy_by_id = {str(row.get(id_field, "")).strip(): row for row in legacy_rows if row.get(id_field)}

    relational_rows = session.query(model).filter(model.organization_id == org_id).all()
    relational_by_id = {row.external_id: row for row in relational_rows if row.external_id}

    # Reverse lookup so relationship fields can be compared: relational
    # internal id -> external_id, per parent entity referenced.
    reverse_lookup: dict[str, dict[int, str]] = {}
    for _, _, parent_entity in _RELATIONSHIP_FIELDS.get(entity, ()):
        if parent_entity in reverse_lookup:
            continue
        parent_model = _MODEL_BY_ENTITY[parent_entity]
        reverse_lookup[parent_entity] = {
            row.id: row.external_id
            for row in session.query(parent_model).filter(parent_model.organization_id == org_id).all()
        }

    missing_from_relational = sorted(set(legacy_by_id) - set(relational_by_id))
    extra_in_relational = sorted(set(relational_by_id) - set(legacy_by_id))

    matched = 0
    mismatched: list[str] = []
    for record_id in sorted(set(legacy_by_id) & set(relational_by_id)):
        legacy_row = legacy_by_id[record_id]
        relational_row = relational_by_id[record_id]
        row_ok = True
        for legacy_field, relational_attr, kind in _IMPORTANT_FIELDS.get(entity, ()):
            legacy_value = _normalize(legacy_row.get(legacy_field), kind)
            relational_value = _normalize(_enum_value(getattr(relational_row, relational_attr)), kind)
            if legacy_value != relational_value:
                row_ok = False
        for legacy_field, relational_attr, parent_entity in _RELATIONSHIP_FIELDS.get(entity, ()):
            legacy_parent_external = _normalize(legacy_row.get(legacy_field), "text")
            relational_internal_id = getattr(relational_row, relational_attr)
            relational_parent_external = (
                reverse_lookup[parent_entity].get(relational_internal_id) if relational_internal_id else None
            )
            if legacy_parent_external != relational_parent_external:
                row_ok = False
        if row_ok:
            matched += 1
        else:
            mismatched.append(record_id)

    return {
        "legacy_count": len(legacy_by_id),
        "relational_count": len(relational_by_id),
        "matched": matched,
        "mismatch": len(mismatched),
        "missing_from_relational": missing_from_relational,
        "extra_in_relational": extra_in_relational,
        "mismatched_ids": mismatched,
    }


def verify_parity(
    *, org_slug: str = DEFAULT_ORGANIZATION_SLUG, entities: tuple[str, ...] = ENTITY_SYNC_ORDER, engine=None,
) -> dict[str, Any]:
    owns_engine = engine is None
    engine = engine or make_engine()
    report: dict[str, Any] = {"org_slug": org_slug, "by_entity": {}, "ok": True}
    try:
        with session_scope(engine) as session:
            org = get_organization_by_slug(session, org_slug)
            if org is None:
                print(f"No organization found for slug {org_slug!r} — nothing to verify.")
                report["ok"] = False
                return report
            for entity in entities:
                result = verify_entity(session, org.id, entity)
                report["by_entity"][entity] = result
                if result["mismatch"] or result["missing_from_relational"] or result["extra_in_relational"]:
                    report["ok"] = False
                label = entity.replace("_", " ").title()
                print(f"{label}")
                print(f"legacy: {result['legacy_count']}")
                print(f"relational: {result['relational_count']}")
                print(f"matched: {result['matched']}")
                print(f"mismatch: {result['mismatch']}")
                if result["missing_from_relational"]:
                    print(f"  missing from relational: {result['missing_from_relational']}")
                if result["extra_in_relational"]:
                    print(f"  extra in relational (no legacy source): {result['extra_in_relational']}")
                print()
    finally:
        if owns_engine:
            engine.dispose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", dest="org_slug", default=DEFAULT_ORGANIZATION_SLUG)
    parser.add_argument("--entity", dest="entities", action="append", choices=ENTITY_SYNC_ORDER)
    args = parser.parse_args()
    entities = tuple(e for e in ENTITY_SYNC_ORDER if e in (args.entities or ENTITY_SYNC_ORDER))

    print(f"Target database: {get_database_url()}")
    report = verify_parity(org_slug=args.org_slug, entities=entities)
    print("PARITY RESULT:", "PASS" if report["ok"] else "MISMATCH")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
