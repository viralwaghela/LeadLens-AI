"""Audit trail writer for core/identity/'s services.

Every create/disable/role-change call in user_service.py,
organization_service.py, and membership_service.py routes through
record_event() here so the identity layer has a real, tested audit
trail from day one, per Phase 1's objective #10/#11. This is separate
from — and does not replace — the live services/security_service.py
audit log (see core/db/models/identity_audit.py's own docstring).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from core.db.models.identity_audit import IdentityAuditEvent, IdentityEventType

# Defense in depth: even though no caller in this package should ever
# pass a password/secret into `detail`, strip anything with these key
# names before it's written, so a future caller's mistake doesn't leak
# a credential into the audit trail.
_SENSITIVE_KEYS = {"password", "password_hash", "secret", "token", "api_key"}


def _sanitize(detail: Mapping[str, Any] | None) -> str | None:
    if not detail:
        return None
    clean = {k: v for k, v in detail.items() if k.lower() not in _SENSITIVE_KEYS}
    return json.dumps(clean, default=str)


def record_event(
    session: Session,
    *,
    event_type: IdentityEventType,
    organization_id: int | None = None,
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    membership_id: int | None = None,
    detail: Mapping[str, Any] | None = None,
) -> IdentityAuditEvent:
    event = IdentityAuditEvent(
        event_type=event_type,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        membership_id=membership_id,
        detail=_sanitize(detail),
        created_at=datetime.now(timezone.utc),
    )
    session.add(event)
    session.flush()
    return event
