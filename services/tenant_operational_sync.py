"""Phase 5 — tenant-scoped relational shadow copies of the operational
subsystems that historically had NO organization scoping at all:
approvals, the execution queue, security/audit events, and the
scheduler's idempotency ledger.

Same authoritative-write contract as Phase 3's
services/relational_sync_service.py: the legacy `core/memory.py` write
already succeeded by the time any function here runs. This module's
job is a best-effort, tenant-scoped RELATIONAL SHADOW COPY of that
write — it never raises, and a failure here never blocks or rolls back
the legacy operation that triggered it. Uses Phase 0's own
core/db/models/operations.py models (Approval, ExecutionQueueItem,
SecurityAuditEvent, SchedulerAlertLedgerEntry) — no new schema, per
Phase 5's "use/extend existing relational foundations" instruction.

Kill switch: LEADLENS_V2_TENANT_CONTEXT_ENABLED, defaults OFF — matching
every prior phase's rollout discipline. Legacy memory_store remains the
sole authoritative store for these subsystems while this is off (or
even while it's on — this is a shadow copy, not a cutover; nothing
reads from these tables yet in Phase 5).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.models.operations import (
    Approval,
    ApprovalRiskLevel,
    ApprovalStatus,
    ExecutionQueueItem,
    ExecutionStatus,
    SchedulerAlertLedgerEntry,
    SecurityAuditEvent,
)
from core.db.session import make_engine, session_scope
from core.identity.tenant_context import TenantContext, build_transitional_context

_LOG = logging.getLogger(__name__)

TENANT_CONTEXT_ENABLED = os.getenv("LEADLENS_V2_TENANT_CONTEXT_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = make_engine()
    return _ENGINE


def _resolve_context() -> TenantContext | None:
    """Resolves the transitional system context fresh, in its own
    session — used by the sync_* wrapper functions below so every live
    call site (audit_event(), integration_manager_v21.py,
    scheduler/run_scheduled_checks.py) doesn't need to plumb a
    TenantContext through itself yet. Returns None (caller should
    treat as "skip") if even organization resolution fails — never
    fabricates a fallback organization."""
    try:
        engine = _get_engine()
        with session_scope(engine) as session:
            return build_transitional_context(session)
    except Exception:  # noqa: BLE001 - resolution failure must never break the caller
        _LOG.error("tenant_resolution_failed for operational shadow sync.", exc_info=True)
        return None


def _resolve_context_for(organization_id: int | None) -> TenantContext | None:
    """Phase 6.1: approvals and execution-queue items created by
    prepare_execution() now carry their own stamped `organization_id` —
    when present, this is the organization the shadow copy MUST land
    under, never the transitional default (which could misattribute a
    real organization's approval/item to a different org's shadow row
    once more than one organization genuinely exists). Falls back to
    the transitional resolver only when `organization_id` is absent
    entirely — legacy items created before Phase 6.1 shipped, or a
    caller that never had one to begin with. An explicitly-supplied but
    invalid/inactive organization_id returns None (skip) rather than
    silently substituting the transitional org — same fail-closed rule
    as everywhere else in this module."""
    if organization_id is None:
        return _resolve_context()
    try:
        from core.db.models.organization import OrganizationStatus
        from core.identity.organization_service import get_organization
        from core.identity.tenant_context import ActorType, build_system_context

        engine = _get_engine()
        with session_scope(engine) as session:
            org = get_organization(session, organization_id)
            if org is None or org.status != OrganizationStatus.ACTIVE:
                return None
            return build_system_context(
                session, organization_id=org.id, actor_type=ActorType.SYSTEM, source="tenant_operational_sync",
            )
    except Exception:  # noqa: BLE001 - resolution failure must never break the caller
        _LOG.error("tenant_resolution_failed for operational shadow sync (explicit organization_id).", exc_info=True)
        return None


def _safe(fn_name: str, entity: str, fn, *args, **kwargs) -> None:
    if not TENANT_CONTEXT_ENABLED:
        return
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - shadow sync must never break a legacy operation
        _LOG.error("Phase 5 operational shadow sync failed: %s(%s).", fn_name, entity, exc_info=True)


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

def _sync_approval(session: Session, org_id: int, legacy_approval: dict[str, Any]) -> None:
    external_id = str(legacy_approval.get("id", "")).strip()
    if not external_id:
        raise ValueError("approval id is required")
    data = legacy_approval.get("data", {})
    existing = (
        session.query(Approval)
        .filter(Approval.organization_id == org_id, Approval.external_id == external_id)
        .one_or_none()
    )
    if existing is None:
        existing = Approval(organization_id=org_id, external_id=external_id)
        session.add(existing)
    existing.title = str(data.get("title", ""))[:300] or "Untitled"
    existing.department = str(data.get("department") or "")[:100] or None
    existing.risk_level = ApprovalRiskLevel(data.get("risk_level") or "High")
    existing.status = ApprovalStatus(data.get("status") or "Pending")
    existing.execution_external_id = str(data.get("execution_id") or "")[:60] or None
    existing.recommendation_id = str(data.get("recommendation_id") or "")[:60] or None
    session.flush()


def sync_approval(legacy_approval: dict[str, Any]) -> None:
    def _run():
        # core.memory.add_memory_entry() wraps every entry's fields
        # under "data" — organization_id was stamped there by
        # prepare_execution(), not at this dict's top level.
        organization_id = legacy_approval.get("data", {}).get("organization_id")
        context = _resolve_context_for(organization_id)
        if context is None:
            return
        engine = _get_engine()
        with session_scope(engine) as session:
            _sync_approval(session, context.organization_id, legacy_approval)

    _safe("sync_approval", "approval", _run)


# ---------------------------------------------------------------------------
# Execution queue
# ---------------------------------------------------------------------------

def _sync_execution_queue_item(session: Session, org_id: int, legacy_item: dict[str, Any]) -> None:
    external_id = str(legacy_item.get("id", "")).strip()
    if not external_id:
        raise ValueError("execution queue item id is required")

    approval_internal_id = None
    legacy_approval_ext_id = str(legacy_item.get("approval_id") or "").strip()
    if legacy_approval_ext_id:
        approval_row = (
            session.query(Approval)
            .filter(Approval.organization_id == org_id, Approval.external_id == legacy_approval_ext_id)
            .one_or_none()
        )
        approval_internal_id = approval_row.id if approval_row else None

    existing = (
        session.query(ExecutionQueueItem)
        .filter(ExecutionQueueItem.organization_id == org_id, ExecutionQueueItem.external_id == external_id)
        .one_or_none()
    )
    if existing is None:
        existing = ExecutionQueueItem(organization_id=org_id, external_id=external_id)
        session.add(existing)
    existing.provider = str(legacy_item.get("provider", ""))[:40]
    existing.action = str(legacy_item.get("action", ""))[:60]
    existing.payload = json.dumps(legacy_item.get("payload") or {}, ensure_ascii=False, default=str)
    existing.title = str(legacy_item.get("title") or "")[:300] or None
    existing.impact = str(legacy_item.get("impact") or "") or None
    existing.recommendation_id = str(legacy_item.get("recommendation_id") or "")[:60] or None
    existing.approval_id = approval_internal_id
    existing.status = _map_execution_status(legacy_item.get("status"))
    existing.fingerprint = str(legacy_item.get("fingerprint", ""))[:64] or external_id
    existing.result = (
        json.dumps(legacy_item.get("result"), ensure_ascii=False, default=str)
        if legacy_item.get("result") is not None
        else None
    )
    session.flush()


def _map_execution_status(value: Any) -> ExecutionStatus:
    try:
        return ExecutionStatus(value or "Awaiting approval")
    except ValueError:
        return ExecutionStatus.AWAITING_APPROVAL


def sync_execution_queue_item(legacy_item: dict[str, Any]) -> None:
    def _run():
        # Execution-queue item dicts are flat (unlike approvals, which
        # are wrapped by core.memory.add_memory_entry()) — organization_id
        # was stamped directly on this dict by prepare_execution().
        context = _resolve_context_for(legacy_item.get("organization_id"))
        if context is None:
            return
        engine = _get_engine()
        with session_scope(engine) as session:
            _sync_execution_queue_item(session, context.organization_id, legacy_item)

    _safe("sync_execution_queue_item", "execution_queue_item", _run)


# ---------------------------------------------------------------------------
# Security / audit events — append-only, never upserted
# ---------------------------------------------------------------------------

def sync_audit_event(actor: str, action: str, entity: str, detail: str = "") -> None:
    def _run():
        context = _resolve_context()
        if context is None:
            return
        engine = _get_engine()
        with session_scope(engine) as session:
            session.add(
                SecurityAuditEvent(
                    organization_id=context.organization_id,
                    actor=str(actor or "")[:120] or None,
                    action=str(action or "")[:80] or None,
                    entity=str(entity or "")[:80] or None,
                    detail=str(detail or "")[:2000] or None,
                    recorded_at=datetime.now(timezone.utc),
                )
            )

    _safe("sync_audit_event", "security_audit_event", _run)


# ---------------------------------------------------------------------------
# Scheduler idempotency ledger
# ---------------------------------------------------------------------------

def sync_scheduler_alert_ledger_entry(check_name: str, item_key: str, note: str = "") -> None:
    """Idempotent by construction: SchedulerAlertLedgerEntry's own
    UniqueConstraint(organization_id, check_name, item_key) — from
    Phase 0 — means a duplicate insert attempt raises IntegrityError,
    caught here like any other shadow-sync failure (harmless: it means
    this exact org+check+item was already recorded, which is exactly
    the outcome idempotency is supposed to produce)."""
    def _run():
        context = _resolve_context()
        if context is None:
            return
        engine = _get_engine()
        with session_scope(engine) as session:
            existing = (
                session.query(SchedulerAlertLedgerEntry)
                .filter(
                    SchedulerAlertLedgerEntry.organization_id == context.organization_id,
                    SchedulerAlertLedgerEntry.check_name == check_name,
                    SchedulerAlertLedgerEntry.item_key == item_key,
                )
                .one_or_none()
            )
            if existing is not None:
                return
            session.add(
                SchedulerAlertLedgerEntry(
                    organization_id=context.organization_id,
                    check_name=check_name[:100],
                    item_key=item_key[:200],
                    note=note or None,
                )
            )

    _safe("sync_scheduler_alert_ledger_entry", "scheduler_alert_ledger", _run)
