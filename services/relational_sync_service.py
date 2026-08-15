"""Phase 3 — CRM relational shadow-write synchronization.

Maps a legacy CRM entity (services/clinic_data_service.py's dict-shaped
rows) onto its Phase 0 relational model (core/db/models/clinic.py) and
upserts it, org-scoped, preserving the legacy business ID as
`external_id`. This is the ONLY place relational persistence logic for
CRM entities lives — clinic_data_service.py calls `sync_upsert()` after
a legacy write succeeds; it does not itself know about SQLAlchemy or
organizations.

Authoritative-write contract (see docs/V2_PHASE3_CRM_DUAL_WRITE.md):
the legacy `core/memory.py` / `memory_store` write is already committed
and successful by the time this module is ever called. This module's
job is best-effort synchronization of a SHADOW store — it must never
cause a legacy CRM operation to fail or roll back. `sync_upsert()`
therefore never raises: every failure is caught, classified, and
recorded via `core.db.models.shadow_sync.ShadowSyncFailure` so it is
visible and repairable (see scripts/repair_v2_crm.py) rather than
silently lost.

Kill switch: LEADLENS_V2_DUAL_WRITE_ENABLED, read once at import time.
Deliberately defaults to OFF — Phase 3 is the first phase to touch a
live write path, and no production deployment has had a backfill run
against it yet at the moment this code first ships (see the deployment
sequence in docs/V2_PHASE3_CRM_DUAL_WRITE.md: deploy with dual-write
off -> bootstrap org -> backfill -> verify parity -> THEN enable).
Turning it off restores exactly Phase 2's CRM behavior — no relational
read, write, or organization resolution is attempted at all, and reads
are never affected (Phase 3 does not touch any CRM read path).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.models.clinic import (
    Appointment,
    AppointmentStatus,
    CorporateClient,
    CorporateClientStatus,
    Lead,
    LeadSource,
    LeadStatus,
    Package,
    PackageStatus,
    PackageTemplate,
    PackageTemplateStatus,
    Patient,
    PatientStatus,
    Payment,
    PaymentStatus,
    ProgressNote,
    Therapist,
    TherapistStatus,
)
from core.db.models.shadow_sync import ShadowSyncFailure
from core.db.session import make_engine, session_scope
from core.identity.default_organization import resolve_default_organization_id

_LOG = logging.getLogger(__name__)

DUAL_WRITE_ENABLED = os.getenv("LEADLENS_V2_DUAL_WRITE_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}

# Dependency-respecting sync order (see docs/V2_PHASE3_CRM_DUAL_WRITE.md's
# "migration order" section) — independent entities first, then entities
# that reference them. Used by scripts/backfill_v2_crm.py; sync_upsert()
# itself is called per-mutation so order doesn't apply there, but a
# MissingParentError is exactly what happens if a dependent entity's
# parent has not been synced yet (see below).
ENTITY_SYNC_ORDER = (
    "leads",
    "corporate_clients",
    "therapists",
    "package_templates",
    "patients",
    "packages",
    "appointments",
    "progress_notes",
    "payments",
)

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = make_engine()
    return _ENGINE


class MissingParentError(Exception):
    """Raised when a dependent entity's required parent hasn't been
    synced to the relational store yet (e.g. an appointment for a
    patient that only exists in the legacy store so far)."""


class UnsupportedEntityError(Exception):
    """Raised for an entity name relational_sync_service does not know
    how to map — should never happen for entities clinic_data_service.py
    actually calls sync_upsert() for."""


# ---------------------------------------------------------------------------
# Small conversion helpers — legacy values are always strings/plain JSON
# types; V2 columns are typed (Date, Numeric, Enum).
# ---------------------------------------------------------------------------

def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _to_decimal(value: Any) -> Decimal:
    """Financial values go through Decimal(str(x)), never Decimal(float),
    so e.g. 19.99 round-trips exactly instead of picking up binary-float
    representation error — see docs/V2_PHASE3_CRM_DUAL_WRITE.md's
    financial-integrity section."""
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


# ---------------------------------------------------------------------------
# Generic get-or-create by (organization_id, external_id)
# ---------------------------------------------------------------------------

def _get_or_create(session: Session, model, org_id: int, external_id: str):
    """Does NOT flush — the caller (a _sync_* function) still needs to
    set every NOT NULL field on a newly-created instance before this is
    safe to send to the database. sync_one() flushes once, after the
    handler has finished setting every field."""
    existing = (
        session.query(model)
        .filter(model.organization_id == org_id, model.external_id == external_id)
        .one_or_none()
    )
    if existing is not None:
        return existing
    instance = model(organization_id=org_id, external_id=external_id)
    session.add(instance)
    return instance


def _resolve_parent_id(
    session: Session, model, org_id: int, legacy_parent_id: Any, *, required: bool, parent_label: str
) -> int | None:
    """`required` governs only whether a MISSING/empty parent id is
    acceptable (e.g. an appointment with no therapist assigned yet).
    Once a non-empty parent id IS supplied, it must resolve within this
    exact organization or this always raises — regardless of
    `required` — never silently drops the reference to None. Silently
    dropping an unresolvable-but-supplied id would be indistinguishable
    from "the legacy row really has no therapist", masking exactly the
    class of problem this function exists to catch (a parent that
    hasn't been synced yet, or — the adversarial case — one that
    belongs to a different organization entirely). Found and fixed via
    the Phase 3 audit's tenant-adversarial test: a progress note in Org
    B referencing a therapist that only existed in Org A previously
    synced as therapist_id=None instead of failing."""
    external_id = _text_or_none(legacy_parent_id)
    if not external_id:
        if required:
            raise MissingParentError(f"no {parent_label} id supplied")
        return None
    row = (
        session.query(model)
        .filter(model.organization_id == org_id, model.external_id == external_id)
        .one_or_none()
    )
    if row is None:
        raise MissingParentError(
            f"{parent_label} {external_id!r} has not been synced to the relational store yet"
        )
    return row.id


# ---------------------------------------------------------------------------
# Per-entity mappers: (session, org_id, legacy_row) -> None
# ---------------------------------------------------------------------------

def _sync_therapist(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("therapist_id"))
    if not external_id:
        raise ValueError("therapist_id is required")
    instance = _get_or_create(session, Therapist, org_id, external_id)
    instance.name = _text_or_none(row.get("name")) or ""
    instance.status = TherapistStatus(row.get("status") or "Active")
    instance.weekly_capacity = int(row.get("weekly_capacity") or 0)


def _sync_patient(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("patient_id"))
    if not external_id:
        raise ValueError("patient_id is required")
    instance = _get_or_create(session, Patient, org_id, external_id)
    instance.name = _text_or_none(row.get("name")) or ""
    instance.email = _text_or_none(row.get("email"))
    instance.phone = _text_or_none(row.get("phone"))
    instance.status = PatientStatus(row.get("status") or "Active")
    instance.last_visit = _parse_date(row.get("last_visit"))
    instance.date_of_birth = _parse_date(row.get("date_of_birth"))
    instance.sessions_remaining = int(row.get("sessions_remaining") or 0)
    instance.consent_to_contact = bool(row.get("consent_to_contact", False))


def _sync_appointment(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("appointment_id"))
    if not external_id:
        raise ValueError("appointment_id is required")
    patient_internal_id = _resolve_parent_id(
        session, Patient, org_id, row.get("patient_id"), required=True, parent_label="patient"
    )
    therapist_internal_id = _resolve_parent_id(
        session, Therapist, org_id, row.get("therapist_id"), required=False, parent_label="therapist"
    )
    instance = _get_or_create(session, Appointment, org_id, external_id)
    instance.patient_id = patient_internal_id
    instance.therapist_id = therapist_internal_id
    instance.appointment_date = _parse_date(row.get("appointment_date"))
    instance.appointment_time = _text_or_none(row.get("appointment_time"))
    instance.service = _text_or_none(row.get("service"))
    instance.status = AppointmentStatus(row.get("status") or "Scheduled")


def _sync_package_template(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("template_id"))
    if not external_id:
        raise ValueError("template_id is required")
    instance = _get_or_create(session, PackageTemplate, org_id, external_id)
    instance.name = _text_or_none(row.get("name")) or ""
    instance.total_sessions = int(row.get("total_sessions") or 0)
    instance.price = _to_decimal(row.get("price"))
    instance.description = _text_or_none(row.get("description"))
    instance.status = PackageTemplateStatus(row.get("status") or "Active")


def _sync_package(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("package_id"))
    if not external_id:
        raise ValueError("package_id is required")
    patient_internal_id = _resolve_parent_id(
        session, Patient, org_id, row.get("patient_id"), required=True, parent_label="patient"
    )
    instance = _get_or_create(session, Package, org_id, external_id)
    instance.patient_id = patient_internal_id
    instance.name = _text_or_none(row.get("name")) or ""
    instance.total_sessions = int(row.get("total_sessions") or 0)
    instance.sessions_remaining = int(row.get("sessions_remaining") or 0)
    instance.start_date = _parse_date(row.get("start_date"))
    instance.expiry_date = _parse_date(row.get("expiry_date"))
    instance.status = PackageStatus(row.get("status") or "Active")


def _sync_payment(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("payment_id"))
    if not external_id:
        raise ValueError("payment_id is required")
    patient_internal_id = _resolve_parent_id(
        session, Patient, org_id, row.get("patient_id"), required=True, parent_label="patient"
    )
    package_internal_id = _resolve_parent_id(
        session, Package, org_id, row.get("package_id"), required=False, parent_label="package"
    )
    instance = _get_or_create(session, Payment, org_id, external_id)
    instance.patient_id = patient_internal_id
    instance.package_id = package_internal_id
    instance.amount = _to_decimal(row.get("amount"))
    instance.payment_date = _parse_date(row.get("payment_date"))
    instance.status = PaymentStatus(row.get("status") or "Paid")
    instance.method = _text_or_none(row.get("method"))
    instance.reference = _text_or_none(row.get("reference"))


def _sync_progress_note(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("progress_id"))
    if not external_id:
        raise ValueError("progress_id is required")
    patient_internal_id = _resolve_parent_id(
        session, Patient, org_id, row.get("patient_id"), required=True, parent_label="patient"
    )
    therapist_internal_id = _resolve_parent_id(
        session, Therapist, org_id, row.get("therapist_id"), required=False, parent_label="therapist"
    )
    instance = _get_or_create(session, ProgressNote, org_id, external_id)
    instance.patient_id = patient_internal_id
    instance.therapist_id = therapist_internal_id
    instance.visit_date = _parse_date(row.get("visit_date"))
    instance.progress_summary = _text_or_none(row.get("progress_summary")) or ""
    instance.progress_status = _text_or_none(row.get("progress_status"))
    pain_score = row.get("pain_score")
    instance.pain_score = int(pain_score) if pain_score not in (None, "") else None
    instance.next_step = _text_or_none(row.get("next_step"))


def _sync_lead(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("lead_id"))
    if not external_id:
        raise ValueError("lead_id is required")
    instance = _get_or_create(session, Lead, org_id, external_id)
    instance.name = _text_or_none(row.get("name")) or ""
    instance.phone = _text_or_none(row.get("phone"))
    instance.email = _text_or_none(row.get("email"))
    instance.message = _text_or_none(row.get("message"))
    instance.source = LeadSource(row.get("source") or "Other")
    instance.status = LeadStatus(row.get("status") or "New")


def _sync_corporate_client(session: Session, org_id: int, row: dict[str, Any]) -> None:
    external_id = _text_or_none(row.get("client_id"))
    if not external_id:
        raise ValueError("client_id is required")
    instance = _get_or_create(session, CorporateClient, org_id, external_id)
    instance.company_name = _text_or_none(row.get("company_name")) or ""
    instance.contact_name = _text_or_none(row.get("contact_name"))
    instance.phone = _text_or_none(row.get("phone"))
    instance.email = _text_or_none(row.get("email"))
    instance.notes = _text_or_none(row.get("notes"))
    instance.status = CorporateClientStatus(row.get("status") or "New")


_SYNC_HANDLERS: dict[str, Callable[[Session, int, dict[str, Any]], None]] = {
    "therapists": _sync_therapist,
    "patients": _sync_patient,
    "appointments": _sync_appointment,
    "package_templates": _sync_package_template,
    "packages": _sync_package,
    "payments": _sync_payment,
    "progress_notes": _sync_progress_note,
    "leads": _sync_lead,
    "corporate_clients": _sync_corporate_client,
}

# entity -> legacy id field name, reused by scripts/ and tests without
# duplicating services/clinic_data_service.py's own ENTITY_META.
LEGACY_ID_FIELD = {
    "therapists": "therapist_id",
    "patients": "patient_id",
    "appointments": "appointment_id",
    "package_templates": "template_id",
    "packages": "package_id",
    "payments": "payment_id",
    "progress_notes": "progress_id",
    "leads": "lead_id",
    "corporate_clients": "client_id",
}


def _classify_error(exc: Exception) -> tuple[str, str]:
    """Returns (error_category, safe_error_summary) — never includes the
    row's own data, only the exception's type and entity-agnostic
    message, so patient/financial details never land in the failure
    ledger."""
    if isinstance(exc, MissingParentError):
        return "missing_parent", "required parent record not yet synced"
    if isinstance(exc, UnsupportedEntityError):
        return "unsupported_entity", "entity has no relational mapping"
    if isinstance(exc, ValueError):
        return "validation", "row failed validation during mapping"
    if isinstance(exc, SQLAlchemyError):
        return "db_error", type(exc).__name__
    return "unknown", type(exc).__name__


def record_sync_failure(
    session: Session,
    *,
    organization_id: int | None,
    entity: str,
    external_id: str | None,
    operation: str,
    error_category: str,
    error_summary: str,
) -> None:
    session.add(
        ShadowSyncFailure(
            organization_id=organization_id,
            entity=entity,
            external_id=external_id,
            operation=operation,
            error_category=error_category,
            error_summary=error_summary[:500],
            resolved=False,
            created_at=datetime.now(timezone.utc),
        )
    )


def sync_one(session: Session, org_id: int, entity: str, legacy_row: dict[str, Any]) -> None:
    """Raises on failure — used by scripts/backfill_v2_crm.py and
    scripts/repair_v2_crm.py, which need to know per-record whether the
    sync actually succeeded so they can report it. The live dual-write
    hook (sync_upsert(), below) wraps this and never lets an exception
    escape."""
    handler = _SYNC_HANDLERS.get(entity)
    if handler is None:
        raise UnsupportedEntityError(entity)
    handler(session, org_id, legacy_row)
    session.flush()  # surface any constraint violation now, inside this call


def sync_upsert(entity: str, legacy_row: dict[str, Any], *, operation: str = "create") -> None:
    """The live dual-write entry point — called by
    services/clinic_data_service.py after a legacy write has already
    succeeded. Never raises. `legacy_row` should be the authoritative
    post-write legacy record (not a partial update payload) — see
    docs/V2_PHASE3_CRM_DUAL_WRITE.md's update-semantics section."""
    if not DUAL_WRITE_ENABLED:
        return

    external_id = _text_or_none(legacy_row.get(LEGACY_ID_FIELD.get(entity, "")))
    try:
        engine = _get_engine()
        with session_scope(engine) as session:
            org_id = resolve_default_organization_id(session)
            sync_one(session, org_id, entity, legacy_row)
    except Exception as exc:  # noqa: BLE001 - must never propagate to the CRM caller
        category, summary = _classify_error(exc)
        _LOG.error(
            "V2 shadow sync failed for %s %s (%s): %s",
            entity, external_id, category, summary,
        )
        try:
            engine = _get_engine()
            with session_scope(engine) as session:
                org_id = None
                try:
                    org_id = resolve_default_organization_id(session)
                except Exception:  # noqa: BLE001 - org resolution itself failed
                    pass
                record_sync_failure(
                    session,
                    organization_id=org_id,
                    entity=entity,
                    external_id=external_id,
                    operation=operation,
                    error_category=category,
                    error_summary=summary,
                )
        except Exception:  # noqa: BLE001 - even failure recording must never crash the CRM write
            _LOG.error(
                "V2 shadow sync failure could not even be recorded for %s %s.",
                entity, external_id,
            )
