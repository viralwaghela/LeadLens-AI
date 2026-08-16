import os

from core.memory import add_memory_entry, get_memory_section

ROLE_PERMISSIONS={"Owner":{"view_finance","manage_users","approve_actions","view_patients","edit_patients"},"Therapist":{"view_patients","edit_patients"},"Receptionist":{"view_patients","edit_patients","manage_appointments"},"Viewer":set()}

# Phase 8.1: when on, audit_rows() (the only live human-facing reader —
# see module docstring below) becomes organization-scoped, reading the
# relational SecurityAuditEvent table (already correctly organization-
# scoped since Phase 5/8's tenant_operational_sync fix) instead of the
# single global legacy `security_audit_log` section. Confirmed defect
# this closes: Organization B, with valid audit.view, could otherwise
# see Organization A's audit events (actor/action/entity/detail) through
# this exact function. Defaults OFF, its own independent kill switch —
# see docs/V2_PHASE8_SAAS_ONBOARDING.md's Phase 8.1 addendum. Only takes
# effect when a live organization actually resolves (a real authenticated
# V2 session) — scripts, the scheduler, and legacy/no-session callers are
# unaffected and keep reading the legacy global section exactly as
# before, which remains the documented legacy-compatibility path.
AUDIT_TENANT_AUTHORITATIVE_ENABLED = os.getenv(
    "LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED", ""
).strip().lower() in {"1", "true", "yes"}

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from core.db.session import make_engine

        _ENGINE = make_engine()
    return _ENGINE


def mask_sensitive(value,visible=2):
    value=str(value or ""); return "*"*len(value) if len(value)<=visible*2 else value[:visible]+"*"*(len(value)-visible*2)+value[-visible:]

def audit_event(actor,action,entity,detail=""):
    """The one live write path (system/internal, called from every
    live audit-worthy action across the app — login/logout, CRM
    mutations that call it explicitly, scheduler runs, etc.). Always
    writes the legacy global `security_audit_log` section first,
    unconditionally, for every organization — this is the documented
    legacy-compatibility path (see audit_rows()'s docstring): turning
    AUDIT_TENANT_AUTHORITATIVE_ENABLED off (or running with no live
    session at all) must still produce a working, if global, audit
    trail. The relational shadow-write (sync_audit_event(), Phase 5,
    made organization-accurate by Phase 8's core.identity.live_organization
    fix) is what audit_rows() actually reads from when the new flag is
    on — see below."""
    add_memory_entry("security_audit_log", {"actor":actor,"action":action,"entity":entity,"detail":detail})
    try:
        from services.tenant_operational_sync import sync_audit_event
        sync_audit_event(actor, action, entity, detail)
    except Exception:  # noqa: BLE001 - Phase 5 shadow sync must never break a legacy audit write
        pass


def _resolve_org_scoped_organization_id() -> int | None:
    """Returns an organization id only when audit reads are tenant-
    authoritative AND a real, live organization resolves (an
    authenticated V2 session exists) — never the transitional default,
    since that would silently point every unauthenticated/legacy caller
    at one organization's audit trail instead of correctly falling back
    to the legacy global section. Returns None (meaning: use the legacy
    global path) for scripts, the scheduler, and any call with no live
    session."""
    if not AUDIT_TENANT_AUTHORITATIVE_ENABLED:
        return None
    try:
        from core.auth import current_authenticated_session

        stored = current_authenticated_session()
    except Exception:  # noqa: BLE001 - no live Streamlit runtime
        stored = None
    return stored.organization_id if stored is not None else None


def _organization_scoped_audit_rows(organization_id: int) -> list[dict]:
    from core.db.models.operations import SecurityAuditEvent
    from core.db.session import session_scope

    with session_scope(_get_engine()) as session:
        rows = (
            session.query(SecurityAuditEvent)
            .filter(SecurityAuditEvent.organization_id == organization_id)
            .order_by(SecurityAuditEvent.id.asc())
            .all()
        )
        return [
            {
                "timestamp": row.recorded_at.isoformat(timespec="seconds") if row.recorded_at else "",
                "actor": row.actor or "",
                "action": row.action or "",
                "entity": row.entity or "",
                "detail": row.detail or "",
            }
            for row in rows
        ]


def audit_rows():
    """Human-facing read path: gated by audit.view — this is the only
    live caller (ui/phases_16_to_20.py's show_security_center(), the
    "Settings -> Data protection" tab); nothing internal/system-side
    reads it (audit_event()'s own write path never calls this), so
    gating this one function closes the boundary at its source rather
    than only at the UI layer. A no-op (always allows) when V2 auth is
    off or no live session exists — see services/authorization_guard.py.

    Phase 8.1: when AUDIT_TENANT_AUTHORITATIVE_ENABLED is on and a live
    organization resolves, reads ONLY that organization's rows from the
    relational SecurityAuditEvent shadow table (the relational shadow
    path — organization-scoped since Phase 5, made accurate to the live
    session by Phase 8's live_organization fix) — never the legacy
    global section. Off, or no live session (the legacy compatibility
    path): reads core/memory.py's single global `security_audit_log`
    section exactly as before Phase 8.1, for scripts/system callers and
    any deployment that hasn't opted in."""
    from services.authorization_guard import require_permission

    require_permission("audit.view")

    organization_id = _resolve_org_scoped_organization_id()
    if organization_id is not None:
        return _organization_scoped_audit_rows(organization_id)

    return [
        {"timestamp": row.get("created_at", ""), **row.get("data", {})}
        for row in get_memory_section("security_audit_log")
    ]
