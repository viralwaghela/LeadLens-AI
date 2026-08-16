"""Shadow-sync failure ledger — Phase 3's record of CRM relational
shadow-write failures.

The whole point of Phase 3's architecture is that a legacy CRM write
succeeding and its relational shadow write failing is an EXPECTED,
survivable outcome, not a crash (see services/relational_sync_service.py
and docs/V2_PHASE3_CRM_DUAL_WRITE.md). This table is how that failure
is made visible and repairable rather than silently lost — a clinic
operation must never appear "fully synchronized" when it wasn't.

Deliberately does not store the failed row's payload (patient names,
contact details, clinical notes, financial amounts) — only enough to
locate and re-attempt the sync later: which organization, which entity,
which business/external ID, what kind of failure. See
relational_sync_service.py's `_classify_error()` for how error_summary
is built without embedding row data.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class ShadowSyncFailure(Base):
    __tablename__ = "shadow_sync_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable: organization resolution itself can fail, in which case
    # there is no organization_id to attach this row to yet.
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    entity: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)  # "create" | "update"
    error_category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    error_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReadMismatch(Base):
    """Phase 4's record of CRM relational shadow-COMPARE mismatches —
    the read-side counterpart to ShadowSyncFailure above. Written by
    services/crm_read_router.py when LEADLENS_V2_READ_COMPARE is on and
    a legacy read and its normalized relational equivalent disagree, or
    when the relational read itself raised. Never stores the actual
    record payload (patient names, contact details, clinical notes,
    financial amounts) — only enough to locate and re-investigate:
    which organization, entity, operation ("list"/"get"), record
    identifier if safe, and a category describing what kind of mismatch
    it was (e.g. "count_mismatch", "field_mismatch:status",
    "relational_read_error")."""

    __tablename__ = "read_mismatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    entity: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)  # "list" | "get"
    mismatch_category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
