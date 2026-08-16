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
    return [
        {"timestamp": row.get("created_at", ""), **row.get("data", {})}
        for row in get_memory_section("security_audit_log")
    ]
