"""Phase 7 — the single backend RBAC chokepoint.

`require_permission(permission)` is the one function every gated
service-layer mutation/administration call goes through. It is a no-op
(always allows) in exactly two cases, both deliberate:

  1. `LEADLENS_V2_AUTH_ENABLED` is off — RBAC enforcement is new,
     additive behavior that only activates once real per-user identity
     exists; it must never break the legacy shared-password deployment
     mode (see core/auth.py).
  2. No live, currently-authenticated V2 Streamlit session exists for
     this call — covers scripts (scripts/migrate_integration_credentials.py,
     scripts/bootstrap_identity.py), the test suite, and Phase 5's
     system/scheduler actors, none of which are a human operating the
     live UI and none of which this phase's RBAC is meant to restrict
     (system actors remain tenant-scoped by Phase 5's TenantContext
     design, which is a separate, already-enforced boundary — see
     docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md).

When a live authenticated session DOES exist and V2 auth is on, this is
the real, backend-authoritative boundary described throughout
docs/V2_PHASE7_AUTH_CUTOVER.md — UI-level button hiding is a courtesy on
top of this, never a substitute for it.
"""
from __future__ import annotations

from core.auth import v2_auth_enabled


class PermissionDenied(Exception):
    def __init__(self, permission: str) -> None:
        self.permission = permission
        super().__init__(f"Permission denied: {permission!r} is required for this action.")


def current_permissions() -> frozenset[str] | None:
    """Returns the live session's permission set, or None if RBAC is not
    active for this call (see module docstring) — never raises."""
    if not v2_auth_enabled():
        return None
    try:
        from core.auth import current_authenticated_session

        session = current_authenticated_session()
    except Exception:  # noqa: BLE001 - no live Streamlit runtime, or any resolution failure
        return None
    if session is None:
        return None
    return session.permissions


def require_permission(permission: str) -> None:
    """Raises PermissionDenied if a live, authenticated session exists
    and lacks `permission`. No-ops otherwise — see module docstring for
    exactly when that is."""
    permissions = current_permissions()
    if permissions is None:
        return
    if permission not in permissions:
        raise PermissionDenied(permission)


def has_permission(permission: str) -> bool:
    """Non-raising check for UI-level visibility decisions (hide a
    button, skip a nav item). Never the authoritative check — see
    require_permission() for that. Returns True when RBAC is not active
    for this call (nothing to hide), matching require_permission()'s
    own no-op behavior in that case."""
    permissions = current_permissions()
    if permissions is None:
        return True
    return permission in permissions
