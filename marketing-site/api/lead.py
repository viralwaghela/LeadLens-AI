"""Vercel serverless function: receives the marketing site's booking form
and writes a new lead directly into LeadLens CareOS's database.

Deliberately self-contained rather than importing core/memory.py or
services/clinic_data_service.py from the main app — those live outside
this Vercel project's root directory (marketing-site/), and relying on
Vercel to bundle files from outside its own project root is fragile.
This mirrors the same table/locking pattern (a single memory_store row,
SELECT ... FOR UPDATE before read-modify-write) so a form submission can
never race with the main app writing at the same moment. If that schema
ever changes in core/memory.py, this needs to be updated to match.

Requires one environment variable, set in the Vercel project's own
settings (never committed): DATABASE_URL — the exact same Postgres
connection string configured as this clinic's DATABASE_URL in its
Streamlit Cloud app secrets. Using any other database here would create
leads nobody in the CRM ever sees.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler

MAX_NAME = 200
MAX_CONTACT = 60
MAX_MESSAGE = 2000


def _send(handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(payload)


def _next_lead_id(leads: list[dict]) -> str:
    next_num = 1
    for item in leads:
        lead_id = str(item.get("lead_id", ""))
        if lead_id.startswith("L-"):
            try:
                next_num = max(next_num, int(lead_id.split("-", 1)[1]) + 1)
            except (IndexError, ValueError):
                continue
    return f"L-{next_num:03d}"


def _insert_lead(database_url: str, name: str, phone: str, email: str, message: str) -> str:
    import psycopg2  # imported lazily so the function still loads without it during local edits

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                # Same guard as core/memory.py's _pg_atomic_update: bounded
                # wait for the row lock rather than hanging indefinitely if
                # the main app happens to be mid-write at the same instant.
                cur.execute("SET LOCAL lock_timeout = '10s'")
                cur.execute("SELECT payload FROM memory_store WHERE id = 1 FOR UPDATE")
                row = cur.fetchone()
                payload = json.loads(row[0]) if row and row[0] else {}
                leads = payload.setdefault("clinic_leads", [])

                new_id = _next_lead_id(leads)
                now = datetime.now().isoformat(timespec="seconds")
                leads.append({
                    "lead_id": new_id,
                    "name": name,
                    "phone": phone,
                    "email": email,
                    "message": message,
                    "source": "Website",
                    "status": "New",
                    "created_at": now,
                    "updated_at": now,
                })

                encoded = json.dumps(payload, ensure_ascii=False)
                if row is None:
                    cur.execute(
                        "INSERT INTO memory_store (id, payload, updated_at, version) "
                        "VALUES (1, %s, %s, 1)",
                        (encoded, now),
                    )
                else:
                    cur.execute(
                        "UPDATE memory_store SET payload = %s, version = version + 1, "
                        "updated_at = %s WHERE id = 1",
                        (encoded, now),
                    )
        return new_id
    finally:
        conn.close()


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            _send(self, 400, {"error": "That request didn't look like a valid form submission."})
            return

        if not isinstance(data, dict):
            _send(self, 400, {"error": "That request didn't look like a valid form submission."})
            return

        # Honeypot: a real visitor never fills this hidden field; a bot
        # filling every input on the form will. Silently pretend success
        # rather than telling a bot its submission was rejected.
        if str(data.get("website", "")).strip():
            _send(self, 200, {"ok": True, "lead_id": None})
            return

        name = str(data.get("name", "")).strip()[:MAX_NAME]
        phone = str(data.get("phone", "")).strip()[:MAX_CONTACT]
        email = str(data.get("email", "")).strip()[:MAX_CONTACT]
        message = str(data.get("message", "")).strip()[:MAX_MESSAGE]

        if not name:
            _send(self, 400, {"error": "Please tell us your name."})
            return
        if not phone and not email:
            _send(self, 400, {"error": "Please share a phone number or email so we can reach you."})
            return

        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            _send(self, 500, {"error": "The booking form isn't fully set up yet — please WhatsApp or call us instead."})
            return

        try:
            lead_id = _insert_lead(database_url, name, phone, email, message)
        except Exception:
            _send(self, 500, {"error": "We couldn't save your enquiry just now — please WhatsApp or call us instead."})
            return

        _send(self, 200, {"ok": True, "lead_id": lead_id})
