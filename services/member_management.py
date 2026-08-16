"""Phase 7 — minimal member management, backed entirely by Phase 1's
existing core/identity/ services (no new identity logic invented here).

Every function takes a `TenantContext` and operates ONLY on that
context's own organization_id — there is no function anywhere in this
module that accepts a caller-supplied organization_id, so an admin can
never manage membership in another organization (spec section 23).
Gated by the same RBAC chokepoint (services/authorization_guard.py)
every other Phase 7 boundary uses; a no-op when V2 auth is off or no
live session exists, exactly like every other guarded function.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.db.models.identity import Membership, MembershipRole, MembershipStatus, User
from core.identity.membership_service import (
    MembershipNotFoundError,
    change_role as _change_role,
    create_membership,
    disable_membership as _disable_membership,
    get_membership,
    list_memberships_for_user,
)
from core.identity.tenant_context import TenantContext
from core.identity.user_service import DuplicateUserError, create_user, get_user_by_email
from services.authorization_guard import require_permission


class ForeignOrganizationError(Exception):
    """Raised when a membership_id resolves to a different organization
    than the caller's TenantContext — never silently ignored or treated
    as 'not found', so a misuse of this API is loud, not silent."""


class LastOwnerError(Exception):
    """Raised when an action would leave an organization with zero
    active OWNER memberships (spec section 24)."""


@dataclass(frozen=True)
class MemberRow:
    membership_id: int
    user_id: int
    email: str
    role: str
    status: str


def _organization_members(session: Session, organization_id: int) -> list[Membership]:
    from sqlalchemy import select

    return list(
        session.scalars(
            select(Membership).where(Membership.organization_id == organization_id)
        )
    )


def list_members(session: Session, tenant_context: TenantContext) -> list[MemberRow]:
    require_permission("members.view")
    rows = []
    for membership in _organization_members(session, tenant_context.organization_id):
        user = session.get(User, membership.user_id)
        rows.append(
            MemberRow(
                membership_id=membership.id,
                user_id=membership.user_id,
                email=user.email if user else "",
                role=membership.role.value,
                status=membership.status.value,
            )
        )
    return rows


def _require_same_organization(membership: Membership, tenant_context: TenantContext) -> None:
    if membership.organization_id != tenant_context.organization_id:
        raise ForeignOrganizationError(
            f"membership {membership.id!r} does not belong to organization {tenant_context.organization_id!r}"
        )


def _active_owner_count(session: Session, organization_id: int) -> int:
    return sum(
        1
        for m in _organization_members(session, organization_id)
        if m.role == MembershipRole.OWNER and m.status == MembershipStatus.ACTIVE
    )


def create_member(
    session: Session,
    tenant_context: TenantContext,
    *,
    email: str,
    password: str,
    role: MembershipRole,
) -> MemberRow:
    """Creates (or reuses, by email) a User, then an ACTIVE Membership
    scoped to `tenant_context.organization_id` — never any other
    organization. Reusing an existing user by email never touches their
    password (see core.identity.user_service.create_user's own
    duplicate-email guard)."""
    require_permission("members.manage")
    user = get_user_by_email(session, email)
    if user is None:
        try:
            user = create_user(session, email=email, password=password)
        except DuplicateUserError:
            user = get_user_by_email(session, email)
    membership = create_membership(
        session, user_id=user.id, organization_id=tenant_context.organization_id,
        role=role, actor_user_id=tenant_context.user_id,
    )
    return MemberRow(
        membership_id=membership.id, user_id=user.id, email=user.email,
        role=membership.role.value, status=membership.status.value,
    )


def change_member_role(
    session: Session, tenant_context: TenantContext, membership_id: int, new_role: MembershipRole,
) -> MemberRow:
    require_permission("members.manage")
    membership = get_membership(session, membership_id)
    if membership is None:
        raise MembershipNotFoundError(f"no membership with id {membership_id!r}")
    _require_same_organization(membership, tenant_context)

    if (
        membership.role == MembershipRole.OWNER
        and new_role != MembershipRole.OWNER
        and membership.status == MembershipStatus.ACTIVE
        and _active_owner_count(session, tenant_context.organization_id) <= 1
    ):
        raise LastOwnerError("cannot change the role of the organization's last active OWNER")

    updated = _change_role(session, membership_id, new_role, actor_user_id=tenant_context.user_id)
    user = session.get(User, updated.user_id)
    return MemberRow(
        membership_id=updated.id, user_id=updated.user_id, email=user.email if user else "",
        role=updated.role.value, status=updated.status.value,
    )


def disable_member(session: Session, tenant_context: TenantContext, membership_id: int) -> MemberRow:
    require_permission("members.manage")
    membership = get_membership(session, membership_id)
    if membership is None:
        raise MembershipNotFoundError(f"no membership with id {membership_id!r}")
    _require_same_organization(membership, tenant_context)

    if (
        membership.role == MembershipRole.OWNER
        and membership.status == MembershipStatus.ACTIVE
        and _active_owner_count(session, tenant_context.organization_id) <= 1
    ):
        raise LastOwnerError("cannot disable the organization's last active OWNER")

    updated = _disable_membership(session, membership_id, actor_user_id=tenant_context.user_id)
    user = session.get(User, updated.user_id)
    return MemberRow(
        membership_id=updated.id, user_id=updated.user_id, email=user.email if user else "",
        role=updated.role.value, status=updated.status.value,
    )
