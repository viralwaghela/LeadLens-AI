"""AuthenticatedIdentity — the tenant-context object Phase 1 builds for
later phases (CRM, Jarvis, agents, automations, approvals, integrations)
to eventually consume. Not wired into any of those yet.

Always produced from trusted, database-backed identity/membership
information via core.identity.authorization_service — never constructed
directly from arbitrary client input.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.db.models.identity import MembershipRole


@dataclass(frozen=True)
class AuthenticatedIdentity:
    user_id: int
    organization_id: int
    membership_id: int
    role: MembershipRole
    permissions: frozenset[str]

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions
