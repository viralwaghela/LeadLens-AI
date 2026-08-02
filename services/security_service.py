import hashlib,json
from pathlib import Path
from datetime import datetime
BASE=Path(__file__).resolve().parents[1]/"data"/"security"; BASE.mkdir(parents=True,exist_ok=True); AUDIT=BASE/"audit_log.json"
ROLE_PERMISSIONS={"Owner":{"view_finance","manage_users","approve_actions","view_patients","edit_patients"},"Therapist":{"view_patients","edit_patients"},"Receptionist":{"view_patients","edit_patients","manage_appointments"},"Viewer":set()}
def mask_sensitive(value,visible=2):
    value=str(value or ""); return "*"*len(value) if len(value)<=visible*2 else value[:visible]+"*"*(len(value)-visible*2)+value[-visible:]
def audit_event(actor,action,entity,detail=""):
    rows=json.loads(AUDIT.read_text(encoding="utf-8") or "[]") if AUDIT.exists() else []; rows.append({"timestamp":datetime.now().isoformat(timespec="seconds"),"actor":actor,"action":action,"entity":entity,"detail":detail}); AUDIT.write_text(json.dumps(rows[-1000:],indent=2),encoding="utf-8")
def audit_rows(): return json.loads(AUDIT.read_text(encoding="utf-8") or "[]") if AUDIT.exists() else []
