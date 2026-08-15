"""Jarvis learning-memory persistence foundation — deliberately minimal.

services/jarvis_memory.py (read in full for this phase) currently stores
five related but distinctly-shaped record types in one flat JSON file
(data/learning/learning_memory.json, not even in core/memory.py yet):
preferences, recommendations, outcomes, executions, and patterns (the
last of which is fully *derived* from outcomes on every write via
_derive_patterns(), not independently authored data).

Fully normalizing that into five separate typed tables now — before any
actual migration is happening, and before real usage patterns under
multi-tenancy are known — is exactly the kind of premature schema
invention this phase is warned against, and the task's own instructions
give an explicit escape hatch for this: "If adding the model risks scope
creep, document it and defer to Phase 2."

What's added instead: one dormant, deliberately generic table that can
hold any of the four *authored* record types (preferences,
recommendations, outcomes, executions — not patterns, which should stay
computed rather than stored, in whichever phase actually migrates this)
behind a `record_type` discriminator and a JSON `payload`, org-scoped and
with a `fingerprint` column mirroring jarvis_memory.py's own dedup
fingerprints (track_recommendation() and record_action_execution() both
already fingerprint/dedupe their rows the same way). This is enough to
prove the storage pattern (org-scoped, durable, coexisting with
core/memory.py) without inventing the final per-type column layout.

jarvis_memory.py itself is completely untouched — it still reads/writes
its own flat file exactly as before. Full normalization into typed
tables (JarvisPreference, JarvisRecommendation, JarvisOutcome,
JarvisExecution) is deferred to the phase that actually performs this
migration, once the real query patterns under multi-tenancy are known.
"""
from __future__ import annotations

import enum

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
    recorded_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
