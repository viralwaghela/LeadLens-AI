"""Shared single-tenant bootstrap organization resolution.

Extracted in Phase 3 from services/jarvis_memory.py's own private
copy of this exact logic (Phase 2), so Jarvis's learning-memory shadow
store and Phase 3's CRM relational shadow store resolve to the SAME
organization row — there is only one clinic per deployment today, so
they should never end up as two different bootstrap organizations.

Not live tenant routing — there is no per-request/per-user organization
context anywhere in the live app yet (see CLAUDE.md's multi-tenancy
gap). This is bootstrap plumbing only: a single, fixed, deployment-wide
default organization, resolved fresh (get-or-create by slug) on every
call rather than cached, so tests can swap engines without stale-ID
bugs and so a future real multi-tenant phase has nothing cached to
invalidate.
"""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from core.identity.organization_service import create_organization, get_organization_by_slug

DEFAULT_ORGANIZATION_SLUG = os.getenv("LEADLENS_DEFAULT_ORG_SLUG", "default-clinic")
DEFAULT_ORGANIZATION_NAME = "Default Clinic"


def resolve_default_organization_id(session: Session) -> int:
    org = get_organization_by_slug(session, DEFAULT_ORGANIZATION_SLUG)
    if org is not None:
        return org.id
    org = create_organization(session, name=DEFAULT_ORGANIZATION_NAME, slug=DEFAULT_ORGANIZATION_SLUG)
    return org.id
