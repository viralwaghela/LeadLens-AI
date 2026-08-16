"""Phase 8 — organization-authoritative CRM writes.

Paired with services/crm_read_router.py's TENANT_AUTHORITATIVE_ENABLED
read path. When that flag is on, services/clinic_data_service.py's
add_record()/update_record() route here instead of the legacy global
JSON list — this module writes directly to core/db/models/clinic.py's
organization-scoped relational tables, reusing
services/relational_sync_service.py's per-entity mappers (sync_one()) so
there is exactly one place that knows how to turn a legacy-shaped dict
row into relational columns, not two independently-maintained copies.

External IDs (e.g. "P-001") are generated scoped to the target
organization's own existing rows, not the legacy list's global counter —
this is the fix for two organizations both wanting "P-001": each
organization's numbering is independent, because it is derived only from
that organization's own relational rows.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from core.db.session import session_scope
from core.identity.live_organization import resolve_live_organization_id
from services.crm_read_router import _MODEL_BY_ENTITY, _get_engine, _read_relational_rows
from services.relational_sync_service import LEGACY_ID_FIELD, sync_one


def _resolve_organization_id(organization_id: int | None) -> int:
    if organization_id is not None:
        return organization_id
    with session_scope(_get_engine()) as session:
        return resolve_live_organization_id(session)

_PREFIX_BY_ENTITY = {
    "patients": "P",
    "appointments": "A",
    "packages": "PKG",
    "package_templates": "PKGT",
    "payments": "PAY",
    "therapists": "T",
    "progress_notes": "PRG",
    "leads": "L",
    "corporate_clients": "C",
}


def _next_external_id(session: Session, entity: str, organization_id: int) -> str:
    model = _MODEL_BY_ENTITY[entity]
    prefix = _PREFIX_BY_ENTITY[entity]
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    used: list[int] = []
    for (external_id,) in session.query(model.external_id).filter(model.organization_id == organization_id).all():
        match = pattern.match(str(external_id or ""))
        if match:
            used.append(int(match.group(1)))
    return f"{prefix}-{max(used, default=0) + 1:03d}"


def _row_by_external_id(entity: str, organization_id: int, external_id: str) -> dict[str, Any] | None:
    engine = _get_engine()
    id_field = LEGACY_ID_FIELD[entity]
    with session_scope(engine) as session:
        for row in _read_relational_rows(session, organization_id, entity):
            if str(row.get(id_field)) == str(external_id):
                return row
    return None


def add_row(entity: str, data: dict[str, Any], *, organization_id: int | None = None) -> dict[str, Any]:
    """`data` must already be validated/shaped the same way
    clinic_data_service._validate_record() shapes a legacy row — this
    module does not re-validate business rules, only persists.
    `organization_id`, when omitted, resolves via the live authenticated
    session (falling back to the transitional default), same as
    services/crm_read_router.py's read path."""
    organization_id = _resolve_organization_id(organization_id)
    id_field = LEGACY_ID_FIELD[entity]
    model = _MODEL_BY_ENTITY[entity]
    engine = _get_engine()
    with session_scope(engine) as session:
        external_id = str(data.get(id_field) or "").strip() or _next_external_id(session, entity, organization_id)
        existing = (
            session.query(model.id)
            .filter(model.organization_id == organization_id, model.external_id == external_id)
            .first()
        )
        if existing is not None:
            raise ValueError(f"{external_id} already exists.")
        row = dict(data)
        row[id_field] = external_id
        sync_one(session, organization_id, entity, row)
    result = _row_by_external_id(entity, organization_id, external_id)
    if result is None:  # pragma: no cover - defensive, should be unreachable after a successful sync_one
        raise RuntimeError(f"{entity} {external_id} was written but could not be re-read.")
    return result


def update_row(
    entity: str, record_id: str, updates: dict[str, Any], *, organization_id: int | None = None,
) -> dict[str, Any]:
    organization_id = _resolve_organization_id(organization_id)
    id_field = LEGACY_ID_FIELD[entity]
    model = _MODEL_BY_ENTITY[entity]
    engine = _get_engine()
    with session_scope(engine) as session:
        existing = (
            session.query(model.id)
            .filter(model.organization_id == organization_id, model.external_id == record_id)
            .first()
        )
        if existing is None:
            raise KeyError(f"{entity.rstrip('s').title()} {record_id} was not found.")
        current_row = next(
            (r for r in _read_relational_rows(session, organization_id, entity) if str(r.get(id_field)) == str(record_id)),
            None,
        )
        merged = dict(current_row or {})
        merged.update({k: v for k, v in updates.items() if k not in (id_field, "created_at")})
        merged[id_field] = record_id
        sync_one(session, organization_id, entity, merged)
    result = _row_by_external_id(entity, organization_id, record_id)
    if result is None:  # pragma: no cover - defensive
        raise RuntimeError(f"{entity} {record_id} was updated but could not be re-read.")
    return result
