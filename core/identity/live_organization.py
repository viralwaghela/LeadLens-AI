"""Phase 8 — resolves "the organization this call is scoped to" for
live, per-request code paths (CRM tenant-authoritative storage,
organization-scoped settings).

Prefers the current authenticated V2 session's organization (Phase 7's
core.auth.current_authenticated_session()) so two real organizations get
genuinely separate data; falls back to the transitional single-clinic
default organization resolver (core.identity.default_organization) for
scripts, the scheduler's system actor, and any call with no live
Streamlit session — exactly as every Phase 2-5 resolver already does
when no live human session exists. No caching, no module-level mutable
"current organization" state — resolved fresh on every call, matching
core/identity/tenant_context.py's own explicit design choice.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from core.identity.default_organization import resolve_default_organization_id


def resolve_live_organization_id(session: Session) -> int:
    try:
        from core.auth import current_authenticated_session

        stored = current_authenticated_session()
    except Exception:  # noqa: BLE001 - no live Streamlit runtime, or any resolution failure
        stored = None
    if stored is not None:
        return stored.organization_id
    return resolve_default_organization_id(session)
