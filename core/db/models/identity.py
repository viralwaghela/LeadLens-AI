"""User and Membership — future identity foundation.

Nothing in the live app creates or reads these tables. core/auth.py is
untouched by Phase 0 and remains the actual login gate — see that file's
own docstring. These models exist so a later phase has real user/
membership infrastructure to migrate onto, without inventing it under
time pressure alongside the actual auth cutover.
"""
from __future__ import annotations

import enum

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.db.models.mixins import TimestampMixin


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"), nullable=False, default=UserStatus.ACTIVE
    )

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class MembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class MembershipRole(str, enum.Enum):
    """Deliberately mirrors the role names already defined (but not yet
    enforced anywhere) in services/security_service.py's ROLE_PERMISSIONS
    dict, rather than inventing a new taxonomy Phase 0 would have to
    reconcile with that one later."""

    OWNER = "Owner"
    THERAPIST = "Therapist"
    RECEPTIONIST = "Receptionist"
    VIEWER = "Viewer"


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
