"""Organization CRUD for the Phase 1 identity layer.

Deactivation, not deletion, is the normal way to retire an organization
— see deactivate_organization(). No delete_organization() is provided;
Phase 1 deliberately does not make destructive deletion a routine
operation.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db.models.identity_audit import IdentityEventType
from core.db.models.organization import Organization, OrganizationStatus
from core.identity import audit_service


class DuplicateOrganizationError(Exception):
    """Raised when an organization with the given slug already exists."""


class OrganizationNotFoundError(Exception):
    """Raised when a lookup-by-id finds nothing."""


def create_organization(
    session: Session,
    *,
    name: str,
    slug: str,
    actor_user_id: int | None = None,
) -> Organization:
    existing = session.scalar(select(Organization).where(Organization.slug == slug))
    if existing is not None:
        raise DuplicateOrganizationError(f"an organization with slug {slug!r} already exists")

    org = Organization(name=name, slug=slug, status=OrganizationStatus.ACTIVE)
    session.add(org)
    session.flush()

    audit_service.record_event(
        session,
        event_type=IdentityEventType.ORGANIZATION_CREATED,
        organization_id=org.id,
        actor_user_id=actor_user_id,
        detail={"name": name, "slug": slug},
    )
    return org


def get_organization(session: Session, organization_id: int) -> Organization | None:
    return session.get(Organization, organization_id)


def get_organization_by_slug(session: Session, slug: str) -> Organization | None:
    return session.scalar(select(Organization).where(Organization.slug == slug))


def update_organization(
    session: Session,
    organization_id: int,
    *,
    name: str | None = None,
) -> Organization:
    org = session.get(Organization, organization_id)
    if org is None:
        raise OrganizationNotFoundError(f"no organization with id {organization_id!r}")
    if name is not None:
        org.name = name
    session.flush()
    return org


def activate_organization(
    session: Session, organization_id: int, *, actor_user_id: int | None = None
) -> Organization:
    org = session.get(Organization, organization_id)
    if org is None:
        raise OrganizationNotFoundError(f"no organization with id {organization_id!r}")
    org.status = OrganizationStatus.ACTIVE
    session.flush()
    audit_service.record_event(
        session,
        event_type=IdentityEventType.ORGANIZATION_ACTIVATED,
        organization_id=org.id,
        actor_user_id=actor_user_id,
    )
    return org


def deactivate_organization(
    session: Session, organization_id: int, *, actor_user_id: int | None = None
) -> Organization:
    org = session.get(Organization, organization_id)
    if org is None:
        raise OrganizationNotFoundError(f"no organization with id {organization_id!r}")
    org.status = OrganizationStatus.INACTIVE
    session.flush()
    audit_service.record_event(
        session,
        event_type=IdentityEventType.ORGANIZATION_DEACTIVATED,
        organization_id=org.id,
        actor_user_id=actor_user_id,
    )
    return org
