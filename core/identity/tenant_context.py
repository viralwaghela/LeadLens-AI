"""Phase 5 — TenantContext: the trusted "which organization, acting as
whom" object every tenant-owned operation should carry.

Formalizes, under one name, the same organization-resolution mechanism
Phase 2/3/4 already built and proved
(core.identity.default_organization.resolve_default_organization_id) —
this module does not replace or duplicate that mechanism, it wraps it.
Existing callers of jarvis_memory.py, relational_sync_service.py, and
crm_read_router.py already achieve the invariant this phase asks for
(every tenant-owned read/write resolves the same single trusted
organization, never a caller-supplied one) — they are intentionally
left unchanged rather than refactored to import this module too, since
that would be an unrelated-refactor risk for zero behavioral gain (see
docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md's audit section for the
reasoning per subsystem).

No module-level mutable tenant state exists anywhere in this file —
every function takes a `Session` and returns a fresh, immutable
`TenantContext`. There is no `CURRENT_ORGANIZATION` global to leak
across requests, threads, or scheduler runs.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from core.db.models.identity import MembershipRole
from core.identity.authorization_service import resolve_identity
from core.identity.default_organization import resolve_default_organization_id


class ActorType(str, enum.Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    SCHEDULER = "SCHEDULER"
    AUTOMATION = "AUTOMATION"


@dataclass(frozen=True)
class TenantContext:
    """Immutable. `user_id`/`membership_id`/`role`/`permissions` are
    None for non-USER actor types (scheduler/system/automation jobs
    have no human user behind them — never force a fake one, per
    docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md)."""

    organization_id: int
    actor_type: ActorType
    user_id: int | None = None
    membership_id: int | None = None
    role: MembershipRole | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    source: str = ""

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


def build_system_context(
    session: Session, *, organization_id: int, actor_type: ActorType = ActorType.SYSTEM, source: str = "",
) -> TenantContext:
    """For scheduler/automation/background jobs — no human user. Takes
    an already-resolved organization_id (see
    resolve_transitional_organization_id() below for the transitional
    single-clinic case) rather than resolving one itself, so callers
    are never tempted to pass through unvalidated input here."""
    if actor_type == ActorType.USER:
        raise ValueError("build_system_context() is for non-USER actors — use build_user_context() instead")
    return TenantContext(organization_id=organization_id, actor_type=actor_type, source=source)


def build_user_context(
    session: Session, *, user_id: int, organization_id: int, source: str = "",
) -> TenantContext | None:
    """For a real, authenticated user acting inside a specific
    organization. Delegates entirely to
    core.identity.authorization_service.resolve_identity() — the same
    trusted, tested Phase 1 resolution path (user active -> org active
    -> membership active) — rather than duplicating that logic. Returns
    None (fails closed) if resolution fails for any reason; never
    fabricates a context from unvalidated input. Not wired into any
    live path yet — see docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md's "Live
    Phase 1 auth/RBAC" section for why."""
    decision = resolve_identity(session, user_id=user_id, organization_id=organization_id)
    if not decision.allowed or decision.identity is None:
        return None
    identity = decision.identity
    return TenantContext(
        organization_id=identity.organization_id,
        actor_type=ActorType.USER,
        user_id=identity.user_id,
        membership_id=identity.membership_id,
        role=identity.role,
        permissions=identity.permissions,
        source=source,
    )


def resolve_transitional_organization_id(session: Session) -> int:
    """The explicit, deterministic, non-user-injectable single-clinic
    bridge described in docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md — a
    thin, intentionally trivial wrapper around the exact mechanism
    Phase 2/3/4 already use and proved safe
    (get-or-create by LEADLENS_DEFAULT_ORG_SLUG, resolved fresh, never
    cached, never accepting caller input). Kept as a separate named
    function (rather than importing resolve_default_organization_id
    directly everywhere) so a future phase that removes the transitional
    bridge only has one call path to change."""
    return resolve_default_organization_id(session)


def build_transitional_context(
    session: Session, *, actor_type: ActorType = ActorType.SYSTEM, source: str = "",
) -> TenantContext:
    """The context an existing single-clinic deployment resolves today,
    with live authentication not yet replaced (Phase 5's explicit,
    permitted bridge — see docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md).
    Deterministic and non-user-injectable: the organization comes from
    resolve_transitional_organization_id(), never from a parameter a
    caller could substitute."""
    organization_id = resolve_transitional_organization_id(session)
    return build_system_context(session, organization_id=organization_id, actor_type=actor_type, source=source)
