"""Membership CRUD for the Phase 1 identity layer.

A user may belong to more than one organization (multiple Membership
rows), but never twice to the same organization — enforced both here
(a pre-check) and at the database level (Phase 0's
uq_membership_user_organization constraint), so a race between two
concurrent requests still can't create a duplicate.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models.identity import Membership, MembershipRole, MembershipStatus
from core.db.models.identity_audit import IdentityEventType
from core.identity import audit_service


class DuplicateMembershipError(Exception):
    """Raised when the (user_id, organization_id) pair already has a membership."""


class MembershipNotFoundError(Exception):
    """Raised when a lookup-by-id finds nothing."""


def create_membership(
    session: Session,
    *,
    user_id: int,
    organization_id: int,
    role: MembershipRole,
    status: MembershipStatus = MembershipStatus.ACTIVE,
    actor_user_id: int | None = None,
) -> Membership:
    existing = session.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
        )
    )
    if existing is not None:
        raise DuplicateMembershipError(
            f"user {user_id!r} already has a membership in organization {organization_id!r}"
        )

    membership = Membership(
        user_id=user_id, organization_id=organization_id, role=role, status=status
    )
    session.add(membership)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateMembershipError(
            f"user {user_id!r} already has a membership in organization {organization_id!r}"
        ) from exc

    audit_service.record_event(
        session,
        event_type=IdentityEventType.MEMBERSHIP_CREATED,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        target_user_id=user_id,
        membership_id=membership.id,
        detail={"role": role.value, "status": status.value},
    )
    return membership


def get_membership(session: Session, membership_id: int) -> Membership | None:
    return session.get(Membership, membership_id)


def get_membership_for_user_org(
    session: Session, *, user_id: int, organization_id: int
) -> Membership | None:
    return session.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
        )
    )


def list_memberships_for_user(session: Session, user_id: int) -> list[Membership]:
    return list(session.scalars(select(Membership).where(Membership.user_id == user_id)))


def disable_membership(
    session: Session, membership_id: int, *, actor_user_id: int | None = None
) -> Membership:
    membership = session.get(Membership, membership_id)
    if membership is None:
        raise MembershipNotFoundError(f"no membership with id {membership_id!r}")
    membership.status = MembershipStatus.DISABLED
    session.flush()
    audit_service.record_event(
        session,
        event_type=IdentityEventType.MEMBERSHIP_DISABLED,
        organization_id=membership.organization_id,
        actor_user_id=actor_user_id,
        target_user_id=membership.user_id,
        membership_id=membership.id,
    )
    return membership


def activate_membership(
    session: Session, membership_id: int, *, actor_user_id: int | None = None
) -> Membership:
    membership = session.get(Membership, membership_id)
    if membership is None:
        raise MembershipNotFoundError(f"no membership with id {membership_id!r}")
    membership.status = MembershipStatus.ACTIVE
    session.flush()
    audit_service.record_event(
        session,
        event_type=IdentityEventType.MEMBERSHIP_ACTIVATED,
        organization_id=membership.organization_id,
        actor_user_id=actor_user_id,
        target_user_id=membership.user_id,
        membership_id=membership.id,
    )
    return membership


def change_role(
    session: Session,
    membership_id: int,
    new_role: MembershipRole,
    *,
    actor_user_id: int | None = None,
) -> Membership:
    membership = session.get(Membership, membership_id)
    if membership is None:
        raise MembershipNotFoundError(f"no membership with id {membership_id!r}")
    old_role = membership.role
    membership.role = new_role
    session.flush()
    audit_service.record_event(
        session,
        event_type=IdentityEventType.ROLE_CHANGED,
        organization_id=membership.organization_id,
        actor_user_id=actor_user_id,
        target_user_id=membership.user_id,
        membership_id=membership.id,
        detail={"old_role": old_role.value, "new_role": new_role.value},
    )
    return membership
