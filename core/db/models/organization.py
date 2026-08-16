"""Organization — the core SaaS tenant entity Phase 0 introduces.

Nothing in the live app creates, reads, or references this table yet.
The one exception is scripts/bootstrap_beyond_pain_org.py, an explicit,
idempotent, manually-run script (never imported by the app) — see that
file and docs/V2_COEXISTENCE.md.
"""
from __future__ import annotations

import enum

from sqlalchemy import Enum, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.db.models.mixins import OrgScopedMixin, TimestampMixin


class OrganizationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    status: Mapped[OrganizationStatus] = mapped_column(
        Enum(OrganizationStatus, name="organization_status"),
        nullable=False,
        default=OrganizationStatus.ACTIVE,
    )

    settings: Mapped["OrganizationSettings | None"] = relationship(
        back_populates="organization", uselist=False, cascade="all, delete-orphan"
    )


class OrganizationSettings(OrgScopedMixin, TimestampMixin, Base):
    """One-to-one with Organization. Mirrors the subset of today's single
    `company` profile dict (core/memory.py's DEFAULT_MEMORY["company"],
    edited via ui/data_hub.py's Business profile form) that Phase 0 can
    confidently name as real fields. `extra` is a deliberate JSON
    catch-all for the rest of that dict rather than guessing at every
    possible key up front — the live `company` dict has no fixed schema
    today (services/clinic_data_service.py never validates it), so
    inventing a fully-typed column for every field it might contain would
    be exactly the kind of premature, incompatible schema this phase is
    warned against. Only google_review_link (added this session, used by
    scheduler/run_scheduled_checks.py's google_review_automation) and the
    core identity/financial fields already load-bearing elsewhere are
    promoted to real columns.
    """

    __tablename__ = "organization_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_organization_settings_organization_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_name: Mapped[str | None] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(300))
    google_review_link: Mapped[str | None] = mapped_column(String(500))
    monthly_revenue: Mapped[float | None] = mapped_column()
    monthly_expenses: Mapped[float | None] = mapped_column()
    target_monthly_revenue: Mapped[float | None] = mapped_column()
    extra: Mapped[str | None] = mapped_column(Text)  # JSON-encoded catch-all

    # Phase 8: whether scheduler/outbound automations run for this
    # organization at all. Defaults False so a newly-onboarded
    # organization never fires outbound messages before an operator has
    # deliberately reviewed and enabled it — see
    # scheduler/run_scheduled_checks.py::resolve_scheduler_organizations()
    # and docs/V2_PHASE8_SAAS_ONBOARDING.md.
    automations_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)

    organization: Mapped["Organization"] = relationship(back_populates="settings")
