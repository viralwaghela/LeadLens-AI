"""Operational/automation relational tables — dormant Phase 0 infrastructure.

Mirrors the shape of today's JSON-backed sections in core/memory.py's
DEFAULT_MEMORY: "scheduler_runs" (SchedulerRun), the alert-dedup ledger
built by scheduler/run_scheduled_checks.py's already_flagged()/
_mark_flagged() ("scheduler_alert_ledger" -> SchedulerAlertLedgerEntry,
key format f"{check_name}::{item_key}" preserved as a real unique
constraint here instead of a string key), the "approvals" section +
services/integration_manager_v21.py's execution_queue (Approval +
ExecutionQueueItem), and services/security_service.py's
"security_audit_log" (SecurityAuditEvent).

No live code reads or writes any table in this file. The scheduler and
approval engine are completely unchanged by Phase 0 — see
scheduler/run_scheduled_checks.py and services/integration_manager_v21.py,
neither of which imports anything from core/db/.
"""
from __future__ import annotations

import enum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.db.models.mixins import OrgScopedMixin, TimestampMixin


class SchedulerRun(OrgScopedMixin, Base):
    """One row per (scheduler pass, check) — matches the granularity of
    scheduler/run_scheduled_checks.py's CheckResult dataclass exactly
    (alerts_raised/approvals_queued/messages_sent/skipped_duplicate/
    detail). `run_id` groups every check from the same run_all_checks()
    pass together, since today's single "scheduler_runs" JSON entry
    covers a whole pass, not one check."""

    __tablename__ = "scheduler_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    check_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    alerts_raised: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approvals_queued: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[str | None] = mapped_column(Text)
    ran_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class SchedulerAlertLedgerEntry(OrgScopedMixin, TimestampMixin, Base):
    """Preserves the exact idempotency semantics of
    already_flagged()/_mark_flagged(): the same (check_name, item_key)
    pair for the same organization can only ever exist once, which is
    the entire mechanism that stops an automation from re-queuing the
    same patient/appointment on every run. The real unique constraint
    here is a *stronger* guarantee than today's string-key-in-a-list
    scan, not a weaker one."""

    __tablename__ = "scheduler_alert_ledger"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "check_name", "item_key",
            name="uq_scheduler_ledger_org_check_item",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class ApprovalStatus(str, enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class ApprovalRiskLevel(str, enum.Enum):
    MEDIUM = "Medium"
    HIGH = "High"


class Approval(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_approvals_org_id"),
        UniqueConstraint("organization_id", "external_id", name="uq_approvals_org_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    risk_level: Mapped[ApprovalRiskLevel] = mapped_column(
        Enum(ApprovalRiskLevel, name="approval_risk_level"),
        nullable=False,
        default=ApprovalRiskLevel.HIGH,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status"), nullable=False, default=ApprovalStatus.PENDING
    )
    execution_external_id: Mapped[str | None] = mapped_column(String(60))
    recommendation_id: Mapped[str | None] = mapped_column(String(60))


class ExecutionStatus(str, enum.Enum):
    AWAITING_APPROVAL = "Awaiting approval"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SENT = "Sent"
    SIMULATED = "Simulated"
    FAILED = "Failed"


class ExecutionQueueItem(OrgScopedMixin, TimestampMixin, Base):
    """Mirrors services/integration_manager_v21.py's execution_queue item
    shape exactly, including the fingerprint field that function's own
    dedup logic relies on. `payload`/`result` are JSON-encoded text, same
    representation core/memory.py already uses for these — not
    normalized further, since the payload shape varies per provider
    (calendar/gmail/whatsapp) and forcing it into typed columns here
    would be exactly the kind of invented, premature structure this
    phase is warned against."""

    __tablename__ = "execution_queue_items"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_execution_queue_org_external_id"),
        ForeignKeyConstraint(
            ["organization_id", "approval_id"],
            ["approvals.organization_id", "approvals.id"],
            name="fk_execution_queue_approval_same_org",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(60))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded
    title: Mapped[str | None] = mapped_column(String(300))
    impact: Mapped[str | None] = mapped_column(Text)
    recommendation_id: Mapped[str | None] = mapped_column(String(60))
    approval_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus, name="execution_status"),
        nullable=False,
        default=ExecutionStatus.AWAITING_APPROVAL,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approved_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)  # JSON-encoded


class SecurityAuditEvent(OrgScopedMixin, Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        # Phase 5 audit finding (docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md
        # section 30-equivalent): no live query path reads this table yet
        # (write-only shadow copy), but any future org-scoped audit query
        # feature would otherwise force a full table scan. Added here as
        # a small, safe, additive schema improvement — no query logic
        # built against it in this phase.
        Index("ix_security_audit_events_organization_id", "organization_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str | None] = mapped_column(String(80))
    entity: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
