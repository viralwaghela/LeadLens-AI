from core.memory import add_memory_entry, get_memory_section

ROLE_PERMISSIONS={"Owner":{"view_finance","manage_users","approve_actions","view_patients","edit_patients"},"Therapist":{"view_patients","edit_patients"},"Receptionist":{"view_patients","edit_patients","manage_appointments"},"Viewer":set()}

def mask_sensitive(value,visible=2):
    value=str(value or ""); return "*"*len(value) if len(value)<=visible*2 else value[:visible]+"*"*(len(value)-visible*2)+value[-visible:]

def audit_event(actor,action,entity,detail=""):
    add_memory_entry("security_audit_log", {"actor":actor,"action":action,"entity":entity,"detail":detail})
    try:
        from services.tenant_operational_sync import sync_audit_event
        sync_audit_event(actor, action, entity, detail)
    except Exception:  # noqa: BLE001 - Phase 5 shadow sync must never break a legacy audit write
        pass

def audit_rows():
    """Phase 7.1: gated by audit.view — this is the only live caller
    (ui/phases_16_to_20.py's show_security_center(), the "Settings ->
    Data protection" tab); nothing internal/system-side reads it, so
    gating this one function closes the boundary at its source rather
    than only at the UI layer. A no-op (always allows) when V2 auth is
    off or no live session exists — see services/authorization_guard.py."""
    from services.authorization_guard import require_permission

    require_permission("audit.view")
    return [
        {"timestamp": row.get("created_at", ""), **row.get("data", {})}
        for row in get_memory_section("security_audit_log")
    ]
