"""User and Membership — future identity foundation.

Nothing in the live app creates or reads these tables. core/auth.py is
untouched and remains the actual login gate — see that file's own
docstring. These models exist so a later phase has real user/membership
infrastructure to migrate onto, without inventing it under time pressure
alongside the actual auth cutover.

Phase 1 extends this Phase 0 foundation (see core/identity/ for the
backend services built on top of it):

- UserStatus gained DISABLED (renamed from INACTIVE, to match Phase 1's
  spec vocabulary) and INVITED (for a future onboarding flow — not
  implemented yet, just reserved so the enum doesn't need another
  migration when it is).
- User gained last_login_at, set only by
  core.identity.authentication_service.authenticate() on success.
- MembershipStatus gained DISABLED (renamed from INACTIVE, same reason).
- MembershipRole was expanded from the 4 roles mirrored from
  services/security_service.py (Owner/Therapist/Receptionist/Viewer) to
  Phase 1's smallest-useful SaaS role model: OWNER, ADMIN, RECEPTIONIST,
  PRACTITIONER (renamed from Therapist — provider-neutral term), FINANCE,
  MARKETING, VIEWER. See core/identity/permissions.py for the full
  role -> permission matrix and docs/V2_PHASE1_IDENTITY.md for the
  rationale. This is still a dormant schema (nothing live reads it), so
  renaming enum members here does not change any live behavior — Phase
  0's own test suite (tests/test_phase0_schema.py) was updated in the
  same commit to use the new names.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.db.models.mixins import TimestampMixin


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    INVITED = "INVITED"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"), nullable=False, default=UserStatus.ACTIVE
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")

    def __repr__(self) -> str:  # pragma: no cover - trivial
        # Deliberately excludes password_hash from repr/logging output.
        return f"User(id={self.id!r}, email={self.email!r}, status={self.status!r})"


class MembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class MembershipRole(str, enum.Enum):
    """Phase 1's smallest-useful SaaS role model. See
    core/identity/permissions.py for what each role can actually do, and
    docs/V2_PHASE1_IDENTITY.md for why this list (not a straight copy of
    services/security_service.py's currently-unenforced Owner/Therapist/
    Receptionist/Viewer set) was chosen."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    RECEPTIONIST = "RECEPTIONIST"
    PRACTITIONER = "PRACTITIONER"
    FINANCE = "FINANCE"
    MARKETING = "MARKETING"
    VIEWER = "VIEWER"


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_organization"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role"), nullable=False
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status"),
        nullable=False,
        default=MembershipStatus.ACTIVE,
    )

    user: Mapped["User"] = relationship(back_populates="memberships")

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"Membership(id={self.id!r}, user_id={self.user_id!r}, "
            f"organization_id={self.organization_id!r}, role={self.role!r}, "
            f"status={self.status!r})"
        )
