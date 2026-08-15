"""Standalone future authentication backend: identifier + password ->
user lookup -> password verification -> active-user validation ->
memberships.

Does NOT issue a Streamlit session and is NOT connected to
core.auth.require_login() or any current UI login form. Real Streamlit
integration is a later, explicit phase's job — see
docs/V2_PHASE1_IDENTITY.md. No JWT/token object is introduced here;
Phase 1 stops at "can I authenticate this identifier+password pair and
what does it grant," which is enough to unit-test the whole identity
stack without inventing session machinery Streamlit doesn't need in the
shape a typical SaaS backend would.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from core.db.models.identity import Membership, MembershipStatus, User, UserStatus
from core.identity.password_service import verify_password
from core.identity.user_service import get_user_by_email, record_login
from core.identity.membership_service import list_memberships_for_user


@dataclass(frozen=True)
class AuthenticationResult:
    success: bool
    reason: str
    user: User | None = None
    memberships: list[Membership] = field(default_factory=list)


def authenticate(session: Session, *, email: str, password: str) -> AuthenticationResult:
    user = get_user_by_email(session, email)
    if user is None:
        return AuthenticationResult(False, "user_not_found")

    if not verify_password(password, user.password_hash):
        return AuthenticationResult(False, "invalid_credentials")

    if user.status != UserStatus.ACTIVE:
        return AuthenticationResult(False, "user_disabled")

    active_memberships = [
        m for m in list_memberships_for_user(session, user.id) if m.status == MembershipStatus.ACTIVE
    ]
    record_login(session, user.id)
    return AuthenticationResult(True, "ok", user=user, memberships=active_memberships)
