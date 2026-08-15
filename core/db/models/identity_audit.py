"""Identity/security audit trail for Phase 1's relational identity layer.

This is deliberately separate from core/db/models/operations.py's
SecurityAuditEvent (which is org-scoped via OrgScopedMixin and mirrors
the *live* services/security_service.py audit log). Identity events like
"user created" or "organization created" can happen before any
organization/membership exists yet, so this table's organization_id is
nullable rather than mandatory.

Nothing in the live app writes to or reads this table. core/identity/'s
services are the only writers, and they are not wired into app.py,
dashboard.py, or core/auth.py. This audit trail may remain part of the
V2 relational layer until a later migration phase consolidates identity
and application audit storage — see docs/V2_PHASE1_IDENTITY.md.

Never store passwords, password hashes, or secrets in `detail` —
core/identity/audit_service.py strips known-sensitive keys before
writing, but callers should not rely on that as the only safeguard.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class IdentityEventType(str, enum.Enum):
    USER_CREATED = "USER_CREATED"
    USER_DISABLED = "USER_DISABLED"
    USER_ACTIVATED = "USER_ACTIVATED"
    ORGANIZATION_CREATED = "ORGANIZATION_CREATED"
    ORGANIZATION_ACTIVATED = "ORGANIZATION_ACTIVATED"
    ORGANIZATION_DEACTIVATED = "ORGANIZATION_DEACTIVATED"
    MEMBERSHIP_CREATED = "MEMBERSHIP_CREATED"
    MEMBERSHIP_DISABLED = "MEMBERSHIP_DISABLED"
    MEMBERSHIP_ACTIVATED = "MEMBERSHIP_ACTIVATED"
    ROLE_CHANGED = "ROLE_CHANGED"


class IdentityAuditEvent(Base):
    """Append-only. No updated_at (events are never edited), so this does
    not use TimestampMixin — just its own created_at."""

    __tablename__ = "identity_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[IdentityEventType] = mapped_column(
        Enum(IdentityEventType, name="identity_event_type"), nullable=False, index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    target_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    membership_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("memberships.id", ondelete="SET NULL")
    )
    detail: Mapped[str | None] = mapped_column(Text)  # JSON-encoded, sanitized
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
