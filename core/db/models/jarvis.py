"""Jarvis learning-memory persistence — Phase 2's live durable store.

Originally added dormant in Phase 0 (deliberately minimal — see git
history for that phase's reasoning on why a single generic table beats
five fully-normalized ones before real usage patterns were known).
Phase 2 is the migration phase that docstring predicted, and now
actually reads/writes this table live via services/jarvis_memory.py.

One row per *authored* record (preference, recommendation, outcome, or
execution — not "pattern", which stays derived/computed on every read
via jarvis_memory._derive_patterns(), never independently persisted,
exactly as before). `fingerprint` reuses each legacy JSON row's own
`id` field (e.g. "PREF-A1B2C3D4E5") as the natural, already-unique,
already-stable dedup key — see services/jarvis_memory.py and
docs/V2_PHASE2_JARVIS_MEMORY.md for the full read/write design
(DB-primary once migrated, legacy-JSON fallback before that, and a
permanent compatibility write to the JSON file because
services/jarvis_context.py has its own independent direct-file-read
dependency on it for provenance display).
"""
from __future__ import annotations

import enum

from datetime import datetime

from sqlalchemy import DateTime, Enum, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.db.models.mixins import OrgScopedMixin


class JarvisLearningRecordType(str, enum.Enum):
    PREFERENCE = "preference"
    RECOMMENDATION = "recommendation"
    OUTCOME = "outcome"
    EXECUTION = "execution"


class JarvisLearningRecord(OrgScopedMixin, Base):
    __tablename__ = "jarvis_learning_records"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "record_type", "fingerprint",
            name="uq_jarvis_learning_org_type_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[JarvisLearningRecordType] = mapped_column(
        Enum(JarvisLearningRecordType, name="jarvis_learning_record_type"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str | None] = mapped_column(String(60))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
