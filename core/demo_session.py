"""Public-demo entry: server-side, passwordless, and only ever into the demo.

Visitors click "Explore LeadLens Demo"; the server (this module) — not a
credential the visitor holds — decides to admit them. There is NO password,
token or link that grants entry: the demo user's stored password hash is not
a valid hash, so the ordinary login form cannot authenticate it, and this
function refuses to run at all unless the deployment is in demo mode.

What it builds is the same `AuthenticatedSession` the real login builds, but
derived through the same trusted `resolve_identity()` chain — so the session
carries exactly the DEMO_VIEWER permissions (further capped by the demo
organization's ceiling), and is revalidated against the database on every
access like any other session. It can only ever name the demo organization:
the organization is looked up by the demo slug AND is_demo, never taken from
any input.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models.identity import MembershipRole, User
from core.db.models.organization import Organization
from core.demo_mode import DEMO_ORG_SLUG, DEMO_USER_EMAIL, DemoModeError, demo_mode_enabled
from core.identity.authorization_service import resolve_identity
from core.identity.session import AuthenticatedSession


class DemoNotReady(RuntimeError):
    """The demo tenant has not been seeded (or is misconfigured)."""


def build_demo_session(db_session: Session) -> AuthenticatedSession:
    if not demo_mode_enabled():
        raise DemoModeError("demo entry")  # demo entry does not exist outside a demo deployment

    org = (
        db_session.query(Organization)
        .filter(Organization.slug == DEMO_ORG_SLUG, Organization.is_demo.is_(True))
        .one_or_none()
    )
    user = db_session.query(User).filter(User.email == DEMO_USER_EMAIL).one_or_none()
    if org is None or user is None:
        raise DemoNotReady("the demo tenant has not been seeded")

    decision = resolve_identity(db_session, user_id=user.id, organization_id=org.id)
    if not decision.allowed or decision.identity is None or decision.identity.role != MembershipRole.DEMO_VIEWER:
        raise DemoNotReady("the demo user is not an active DEMO_VIEWER of the demo organization")

    identity = decision.identity
    return AuthenticatedSession(
        user_id=identity.user_id,
        email=user.email,
        organization_id=identity.organization_id,
        organization_name=org.name,
        membership_id=identity.membership_id,
        role=identity.role,
        permissions=identity.permissions,
    )


def is_demo_session(session: AuthenticatedSession | None) -> bool:
    """True only for a session that is exactly a demo-viewer session."""
    return session is not None and session.role == MembershipRole.DEMO_VIEWER
