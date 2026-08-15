"""Shared column mixins for V2 models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    """created_at / updated_at on every table that has them — matches the
    created_at/updated_at fields already present on nearly every entity in
    services/clinic_data_service.py's current JSON records."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


class OrgScopedMixin:
    """Every tenant-owned table gets this — the organization_id column
    plus its foreign key. Indexed on every table that uses it (see each
    model's __table_args__ for the actual Index — this mixin only defines
    the column itself, since index naming/composition needs to be
    explicit per-table for the organization-scoped uniqueness
    constraints Phase 0 requires)."""

    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
