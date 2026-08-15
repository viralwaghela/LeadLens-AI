"""Reusable backend authorization layer — Phase 1's core deliverable.

authorize() performs, in order:
    1. user exists
    2. user is active
    3. organization exists
    4. organization is active
    5. membership exists
    6. membership is active
    7. membership's role grants the requested permission

Any failure short-circuits with a DENIED decision and a machine-readable
reason code. Supplying an organization_id is never, on its own, enough
to gain access — the membership check (5-6) is unconditional.

Not wired into app.py, dashboard.py, ui/, services/, or scheduler/ yet.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.db.models.identity import Membership, MembershipStatus, User, UserStatus
from core.db.models.organization import Organization, OrganizationStatus
from core.identity.context import AuthenticatedIdentity
from core.identity.membership_service import get_membership_for_user_org
from core.identity.permissions import permissions_for_role


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    identity: AuthenticatedIdentity | None = None


def resolve_identity(
    session: Session, *, user_id: int, organization_id: int
) -> AuthorizationDecision:
    """Runs checks 1-6 (everything except the specific-permission check)
    and returns the resulting identity/context if they all pass. Used by
    authorize() and directly by future phases that need "is this user
    allowed into this organization at all" without a specific permission
    in mind yet."""
    user = session.get(User, user_id)
    if user is None:
        return AuthorizationDecision(False, "user_not_found")
    if user.status != UserStatus.ACTIVE:
        return AuthorizationDecision(False, "user_disabled")

    org = session.get(Organization, organization_id)
    if org is None:
        return AuthorizationDecision(False, "organization_not_found")
    if org.status != OrganizationStatus.ACTIVE:
        return AuthorizationDecision(False, "organization_inactive")

    membership = get_membership_for_user_org(
        session, user_id=user_id, organization_id=organization_id
    )
    if membership is None:
        return AuthorizationDecision(False, "membership_not_found")
    if membership.status != MembershipStatus.ACTIVE:
        return AuthorizationDecision(False, "membership_disabled")

    identity = AuthenticatedIdentity(
        user_id=user.id,
        organization_id=org.id,
        membership_id=membership.id,
        role=membership.role,
        permissions=permissions_for_role(membership.role),
    )
    return AuthorizationDecision(True, "allowed", identity=identity)


def authorize(
    session: Session, *, user_id: int, organization_id: int, permission: str
) -> AuthorizationDecision:
    decision = resolve_identity(session, user_id=user_id, organization_id=organization_id)
    if not decision.allowed:
        return decision

    identity = decision.identity
    assert identity is not None  # resolve_identity guarantees this when allowed=True
    if not identity.has_permission(permission):
        return AuthorizationDecision(False, "permission_denied", identity=identity)

    return AuthorizationDecision(True, "allowed", identity=identity)
