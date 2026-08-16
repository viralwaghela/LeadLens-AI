"""Phase 4 — CRM relational read routing.

Single hook point: services/clinic_data_service.py's `_read_rows()` is
the one true "get every row for this entity, unfiltered, legacy-shaped"
chokepoint — `list_records()`, `get_record()`, `search_records()`,
`records_with_patient_names()`, `patient_profile()`,
`patient_risk_summary()`, and `clinic_metrics()` are all built on top
of it (directly or transitively), so hooking `_read_rows()` alone gives
every caller (UI, Jarvis, scheduler, reports) correctly-routed reads
without touching any of them — the same minimal-surface-area approach
Phase 3 used for writes (two call sites covered every mutation).

    read_rows(entity, legacy_reader)
        -> entity's LEADLENS_V2_READ_<ENTITY> flag OFF (default):
           legacy_reader() — behavior is byte-for-byte Phase 3.
           If LEADLENS_V2_READ_COMPARE is on, ALSO reads relational,
           normalizes it back to legacy shape, compares, and records
           any mismatch (core/db/models/shadow_sync.py's
           ReadMismatch) — but still RETURNS the legacy result.
        -> flag ON: relational is authoritative for this entity. Reads
           the relational table, normalizes it to the exact legacy
           dict shape, and returns that. If the relational read fails:
           records the failure, and only falls back to legacy if
           LEADLENS_V2_READ_FAILSAFE_LEGACY is explicitly set — the
           default is to let the error propagate, so a cutover defect
           is immediately visible rather than silently masked.

Every relational query is organization-scoped via the same bootstrap
organization resolution Phase 2/3 already use
(core.identity.default_organization) — never a caller-supplied
organization_id.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.models.clinic import (
    Appointment,
    CorporateClient,
    Lead,
    Package,
    PackageTemplate,
    Patient,
    Payment,
    ProgressNote,
    Service,
    Therapist,
)
from core.db.models.shadow_sync import ReadMismatch
from core.db.session import make_engine, session_scope
from core.identity.default_organization import resolve_default_organization_id
from core.identity.live_organization import resolve_live_organization_id

_LOG = logging.getLogger(__name__)

# Phase 8: when on, relational storage is authoritative for ALL nine CRM
# entities' reads (bypassing the per-entity LEADLENS_V2_READ_<ENTITY>
# flags below, which remain meaningful only for the gradual, per-entity
# Phase 4 rollout when this is off), scoped to the CURRENT live
# organization (core.identity.live_organization) rather than always the
# single transitional default — the fix that makes a second organization's
# CRM data genuinely separate instead of sharing one global legacy list.
# Defaults OFF, its own independent kill switch — see
# docs/V2_PHASE8_SAAS_ONBOARDING.md. Paired with
# services/crm_tenant_writer.py, which clinic_data_service.py's
# add_record()/update_record() route to when this is on.
TENANT_AUTHORITATIVE_ENABLED = os.getenv(
    "LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED", ""
).strip().lower() in {"1", "true", "yes"}


# Every flag defaults OFF — a compelling documented reason would be
# needed to change that, and there isn't one yet: Phase 4 ships with
# zero production read behavior changed until an operator explicitly
# opts an entity in, per docs/V2_PHASE4_READ_CUTOVER.md's runbook.
READ_FLAGS = {
    "leads": "LEADLENS_V2_READ_LEADS",
    "corporate_clients": "LEADLENS_V2_READ_CORPORATE_CLIENTS",
    "services": "LEADLENS_V2_READ_SERVICES",
    "therapists": "LEADLENS_V2_READ_PRACTITIONERS",
    "patients": "LEADLENS_V2_READ_PATIENTS",
    # package_templates is a genuinely separate legacy entity from
    # packages (services/clinic_data_service.py's ENTITY_META lists it
    # on its own, with its own callers in ui/patient_crm.py) — not
    # named in the spec's example flag list, but "each entity must be
    # independently controllable" requires its own switch rather than
    # silently piggybacking on LEADLENS_V2_READ_PACKAGES.
    "package_templates": "LEADLENS_V2_READ_PACKAGE_TEMPLATES",
    "packages": "LEADLENS_V2_READ_PACKAGES",
    "appointments": "LEADLENS_V2_READ_APPOINTMENTS",
    "progress_notes": "LEADLENS_V2_READ_PROGRESS_NOTES",
    "payments": "LEADLENS_V2_READ_PAYMENTS",
}

COMPARE_MODE = os.getenv("LEADLENS_V2_READ_COMPARE", "").strip().lower() in {"1", "true", "yes"}
# Documented default: OFF. Once an entity's read flag is on, a
# relational read failure is allowed to propagate (visible immediately,
# e.g. as a Streamlit error) rather than being silently masked by a
# fallback nobody is watching for. Set this explicitly to restore a
# fail-safe-to-legacy behavior instead — see
# docs/V2_PHASE4_READ_CUTOVER.md.
READ_FAILSAFE_LEGACY = os.getenv("LEADLENS_V2_READ_FAILSAFE_LEGACY", "").strip().lower() in {
    "1", "true", "yes",
}

_MODEL_BY_ENTITY = {
    "therapists": Therapist,
    "patients": Patient,
    "appointments": Appointment,
    "package_templates": PackageTemplate,
    "packages": Package,
    "payments": Payment,
    "progress_notes": ProgressNote,
    "leads": Lead,
    "corporate_clients": CorporateClient,
}

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = make_engine()
    return _ENGINE


def entity_read_enabled(entity: str) -> bool:
    flag_name = READ_FLAGS.get(entity)
    if flag_name is None:
        return False
    return os.getenv(flag_name, "").strip().lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Relational -> legacy-shape normalizers
#
# Every field name/type mirrors services/clinic_data_service.py's own
# _validate_record() output exactly. `archived` is reconstructed
# (legacy rows carry both status="Archived" and archived=True, set
# together by archive_record()) so list_records()'s own archived filter
# — which checks both keys — behaves identically regardless of source.
# created_at/updated_at are intentionally NOT reproduced from the
# legacy value: relational rows get their own timestamps at
# backfill/dual-write time (a real, accepted divergence — see
# docs/V2_PHASE4_READ_CUTOVER.md), so callers that need exact legacy
# timestamps should not rely on this path. Nothing in the live app
# currently does (confirmed in the Phase 4 read-path audit).
# ---------------------------------------------------------------------------

def _iso_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def _iso_dt(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _is_archived(status_value: str) -> bool:
    return str(status_value or "").strip().lower() == "archived"


def _normalize_therapist(row: Therapist) -> dict[str, Any]:
    out = {
        "therapist_id": row.external_id,
        "name": row.name,
        "status": row.status.value,
        "weekly_capacity": row.weekly_capacity,
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _normalize_patient(row: Patient) -> dict[str, Any]:
    out = {
        "patient_id": row.external_id,
        "name": row.name,
        "email": row.email or "",
        "phone": row.phone or "",
        "status": row.status.value,
        "last_visit": _iso_date(row.last_visit),
        "date_of_birth": _iso_date(row.date_of_birth),
        "sessions_remaining": row.sessions_remaining,
        "consent_to_contact": bool(row.consent_to_contact),
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _normalize_appointment(row: Appointment, *, patient_ext: str | None, therapist_ext: str | None) -> dict[str, Any]:
    out = {
        "appointment_id": row.external_id,
        "patient_id": patient_ext or "",
        "therapist_id": therapist_ext or "",
        "appointment_date": _iso_date(row.appointment_date),
        "appointment_time": row.appointment_time or "",
        "status": row.status.value,
        "service": row.service or "",
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _normalize_package_template(row: PackageTemplate) -> dict[str, Any]:
    out = {
        "template_id": row.external_id,
        "name": row.name,
        "total_sessions": row.total_sessions,
        "price": float(row.price),
        "description": row.description or "",
        "status": row.status.value,
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _normalize_package(row: Package, *, patient_ext: str | None) -> dict[str, Any]:
    out = {
        "package_id": row.external_id,
        "patient_id": patient_ext or "",
        "name": row.name,
        "total_sessions": row.total_sessions,
        "sessions_remaining": row.sessions_remaining,
        "start_date": _iso_date(row.start_date),
        "expiry_date": _iso_date(row.expiry_date),
        "status": row.status.value,
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _normalize_payment(row: Payment, *, patient_ext: str | None, package_ext: str | None) -> dict[str, Any]:
    out = {
        "payment_id": row.external_id,
        "patient_id": patient_ext or "",
        "package_id": package_ext or "",
        "amount": float(row.amount),
        "payment_date": _iso_date(row.payment_date),
        "status": row.status.value,
        "method": row.method or "",
        "reference": row.reference or "",
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _normalize_progress_note(row: ProgressNote, *, patient_ext: str | None, therapist_ext: str | None) -> dict[str, Any]:
    return {
        "progress_id": row.external_id,
        "patient_id": patient_ext or "",
        "therapist_id": therapist_ext or "",
        "visit_date": _iso_date(row.visit_date),
        "progress_summary": row.progress_summary or "",
        "progress_status": row.progress_status or "",
        "pain_score": row.pain_score,
        "next_step": row.next_step or "",
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }


def _normalize_lead(row: Lead) -> dict[str, Any]:
    out = {
        "lead_id": row.external_id,
        "name": row.name,
        "phone": row.phone or "",
        "email": row.email or "",
        "message": row.message or "",
        "source": row.source.value,
        "status": row.status.value,
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _normalize_service(row: Service) -> dict[str, Any]:
    """No legacy counterpart exists — services/clinic_data_service.py
    has no "services" entity in ENTITY_META, so there is nothing to
    normalize output *to*; this shape is simply the relational fields
    directly. LEADLENS_V2_READ_SERVICES and this function exist so the
    plumbing is ready (per Phase 4's spec), but nothing in the live app
    calls read_services() yet — see docs/V2_PHASE4_READ_CUTOVER.md."""
    out = {
        "service_id": row.id,
        "name": row.name,
        "description": row.description or "",
        "default_duration_minutes": row.default_duration_minutes,
        "status": row.status.value,
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def read_services() -> list[dict[str, Any]]:
    """Standalone relational-only reader — no legacy_reader fallback is
    possible since there is no legacy "services" entity. Not called by
    any live code path yet (dormant, prepared infrastructure).

    Phase 5 audit finding, fixed here: this previously accepted an
    optional caller-supplied `organization_id`, inconsistent with every
    other relational reader in this module (none of which ever accept
    one — organization is always resolved via trusted context, never
    caller input). Harmless in practice (nothing ever called it with an
    untrusted argument), but tightened before it could become a real
    attack surface if a future caller wired it up carelessly. Organization
    is now always resolved via the same trusted transitional mechanism
    every other reader here uses."""
    engine = _get_engine()
    with session_scope(engine) as session:
        org_id = resolve_default_organization_id(session)
        rows = session.query(Service).filter(Service.organization_id == org_id).order_by(Service.id.asc()).all()
        return [_normalize_service(row) for row in rows]


def _normalize_corporate_client(row: CorporateClient) -> dict[str, Any]:
    out = {
        "client_id": row.external_id,
        "company_name": row.company_name,
        "contact_name": row.contact_name or "",
        "phone": row.phone or "",
        "email": row.email or "",
        "notes": row.notes or "",
        "status": row.status.value,
        "created_at": _iso_dt(row.created_at),
        "updated_at": _iso_dt(row.updated_at),
    }
    if _is_archived(row.status.value):
        out["archived"] = True
    return out


def _read_relational_rows(session: Session, org_id: int, entity: str) -> list[dict[str, Any]]:
    """Ordered by relational id ascending, which — because backfill
    inserts in legacy list order and dual-write appends afterward in
    the same relative order — reproduces legacy list order in the
    normal case (single backfill, then ongoing live creates). See
    docs/V2_PHASE4_READ_CUTOVER.md's ordering section."""
    model = _MODEL_BY_ENTITY[entity]
    rows = session.query(model).filter(model.organization_id == org_id).order_by(model.id.asc()).all()

    if entity in ("patients", "therapists", "package_templates", "leads", "corporate_clients"):
        normalizer = {
            "patients": _normalize_patient,
            "therapists": _normalize_therapist,
            "package_templates": _normalize_package_template,
            "leads": _normalize_lead,
            "corporate_clients": _normalize_corporate_client,
        }[entity]
        return [normalizer(row) for row in rows]

    # Entities with parent relationships need a reverse external_id
    # lookup — resolved once per entity call, not per row.
    def _ext_map(model_cls) -> dict[int, str]:
        return {
            r.id: r.external_id
            for r in session.query(model_cls).filter(model_cls.organization_id == org_id).all()
        }

    if entity == "appointments":
        patients = _ext_map(Patient)
        therapists = _ext_map(Therapist)
        return [
            _normalize_appointment(
                row, patient_ext=patients.get(row.patient_id), therapist_ext=therapists.get(row.therapist_id)
            )
            for row in rows
        ]
    if entity == "packages":
        patients = _ext_map(Patient)
        return [_normalize_package(row, patient_ext=patients.get(row.patient_id)) for row in rows]
    if entity == "payments":
        patients = _ext_map(Patient)
        packages = _ext_map(Package)
        return [
            _normalize_payment(
                row, patient_ext=patients.get(row.patient_id), package_ext=packages.get(row.package_id)
            )
            for row in rows
        ]
    if entity == "progress_notes":
        patients = _ext_map(Patient)
        therapists = _ext_map(Therapist)
        return [
            _normalize_progress_note(
                row, patient_ext=patients.get(row.patient_id), therapist_ext=therapists.get(row.therapist_id)
            )
            for row in rows
        ]
    raise ValueError(f"no relational reader for entity {entity!r}")


# ---------------------------------------------------------------------------
# Compare-mode
# ---------------------------------------------------------------------------

_COMPARE_FIELDS_EXCLUDED = {"created_at", "updated_at"}


def _record_mismatch(
    session: Session, *, organization_id: int | None, entity: str, external_id: str | None,
    operation: str, mismatch_category: str, detail: str,
) -> None:
    session.add(
        ReadMismatch(
            organization_id=organization_id, entity=entity, external_id=external_id,
            operation=operation, mismatch_category=mismatch_category, detail=detail[:500],
            created_at=datetime.now(timezone.utc),
        )
    )


def compare_rows(
    session: Session, org_id: int, entity: str, legacy_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Returns a list of mismatch dicts (empty if none). Also records
    each mismatch via _record_mismatch(). Comparison excludes
    created_at/updated_at (see module docstring) and normalizes
    financial fields via Decimal to avoid float-noise false positives."""
    relational_rows = _read_relational_rows(session, org_id, entity)
    id_field = {
        "therapists": "therapist_id", "patients": "patient_id", "appointments": "appointment_id",
        "package_templates": "template_id", "packages": "package_id", "payments": "payment_id",
        "progress_notes": "progress_id", "leads": "lead_id", "corporate_clients": "client_id",
    }[entity]

    legacy_by_id = {str(r.get(id_field, "")): r for r in legacy_rows if r.get(id_field)}
    relational_by_id = {str(r.get(id_field, "")): r for r in relational_rows if r.get(id_field)}

    mismatches: list[dict[str, Any]] = []

    if len(legacy_by_id) != len(relational_by_id):
        mismatches.append({"category": "count_mismatch", "detail": f"legacy={len(legacy_by_id)} relational={len(relational_by_id)}"})
        _record_mismatch(
            session, organization_id=org_id, entity=entity, external_id=None, operation="list",
            mismatch_category="count_mismatch",
            detail=f"legacy={len(legacy_by_id)} relational={len(relational_by_id)}",
        )

    for record_id, legacy_row in legacy_by_id.items():
        relational_row = relational_by_id.get(record_id)
        if relational_row is None:
            mismatches.append({"category": "missing_from_relational", "id": record_id})
            _record_mismatch(
                session, organization_id=org_id, entity=entity, external_id=record_id, operation="list",
                mismatch_category="missing_from_relational", detail="present in legacy, absent in relational",
            )
            continue
        for key, legacy_value in legacy_row.items():
            if key in _COMPARE_FIELDS_EXCLUDED or key == "archived":
                continue
            relational_value = relational_row.get(key)
            if isinstance(legacy_value, (int, float)) and isinstance(relational_value, (int, float)):
                equal = Decimal(str(legacy_value)) == Decimal(str(relational_value))
            else:
                equal = legacy_value == relational_value
            if not equal:
                mismatches.append({"category": f"field_mismatch:{key}", "id": record_id})
                _record_mismatch(
                    session, organization_id=org_id, entity=entity, external_id=record_id, operation="list",
                    mismatch_category=f"field_mismatch:{key}", detail=f"legacy!=relational for field {key!r}",
                )

    return mismatches


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def read_rows(
    entity: str,
    legacy_reader: Callable[[], list[dict[str, Any]]],
    *,
    organization_id: int | None = None,
) -> list[dict[str, Any]]:
    """`organization_id`, when supplied, is used verbatim instead of
    resolving one — for programmatic multi-org callers (tests,
    scheduler enumeration) that already know exactly which organization
    they're operating for and must not depend on an implicit live
    session. The live Streamlit UI never passes this; it relies on
    resolve_live_organization_id()'s session-based resolution instead."""
    if TENANT_AUTHORITATIVE_ENABLED and entity in _MODEL_BY_ENTITY:
        engine = _get_engine()
        with session_scope(engine) as session:
            org_id = organization_id if organization_id is not None else resolve_live_organization_id(session)
            return _read_relational_rows(session, org_id, entity)

    if entity not in READ_FLAGS:
        return legacy_reader()  # e.g. "services" has no legacy source at all yet

    if not entity_read_enabled(entity):
        legacy_rows = legacy_reader()
        if COMPARE_MODE:
            try:
                engine = _get_engine()
                with session_scope(engine) as session:
                    org_id = resolve_default_organization_id(session)
                    compare_rows(session, org_id, entity, legacy_rows)
            except Exception:  # noqa: BLE001 - compare mode must never break a real read
                _LOG.error("V2 read-compare failed for %s.", entity, exc_info=True)
        return legacy_rows

    try:
        engine = _get_engine()
        with session_scope(engine) as session:
            org_id = resolve_default_organization_id(session)
            return _read_relational_rows(session, org_id, entity)
    except SQLAlchemyError:
        _LOG.error("V2 relational read failed for %s.", entity, exc_info=True)
        if READ_FAILSAFE_LEGACY:
            return legacy_reader()
        raise
