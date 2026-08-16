"""Patient-facing WhatsApp messages tied to appointment events.

Deliberately separate from services/integration_manager_v21.py's
approval-gated flow: appointment confirmations and RSVP reminders are
sent directly, on the founder's explicit instruction, because they're
low-risk transactional messages (wrong date/time is the worst case, not
a financial or clinical error) — unlike everything else in the app,
which stays approval-gated. Every send here still requires the patient's
recorded consent_to_contact and a phone number, same as the gated flow.

Kept structurally separate from any promotional/offer content (see
CLAUDE.md discussion) — WhatsApp Business API treats promotional and
transactional messages differently, and a transactional template getting
flagged as promotional risks the whole clinic's WhatsApp account being
restricted. Nothing in this module should ever have offer/marketing
copy folded into it.
"""
from __future__ import annotations

from typing import Any

from core.memory import add_memory_entry, load_company
from integrations.whatsapp_service import WhatsAppBusinessService
from services.clinic_data_service import get_record


def _whatsapp_client() -> WhatsAppBusinessService:
    """Phase 6: resolve the same trusted transitional TenantContext every
    other Phase 5 hook point uses, so a booking confirmation/reminder
    uses this organization's WhatsApp credentials rather than always
    reading os.environ. Falls back to the plain (env-reading)
    constructor on any resolution failure — a DB hiccup must never
    block a transactional patient message, matching every other
    Phase 5/6 "shadow resolution never blocks the legacy operation"
    hook point."""
    try:
        from core.db.session import session_scope
        from core.identity.tenant_context import build_transitional_context
        from services.integration_clients import get_whatsapp_client
        from services.integration_credentials import _get_engine

        # Same shared-engine reasoning as
        # services/integration_manager_v21.py's _current_tenant_context().
        engine = _get_engine()
        with session_scope(engine) as session:
            context = build_transitional_context(session)
        return get_whatsapp_client(context)
    except Exception:  # noqa: BLE001 - must never block a transactional send
        return WhatsAppBusinessService()


def _clinic_name() -> str:
    return load_company().get("business_name") or "the clinic"


def _log(summary: str, status: str) -> None:
    add_memory_entry(
        "daily_logs",
        {
            "department": "Integrations Agent",
            "summary": summary,
            "status": status,
        },
    )


def _patient_for_appointment(appointment: dict[str, Any]) -> dict[str, Any] | None:
    """Shared consent/phone gate for every auto-send in this module.
    Returns the patient record if it's safe to message them, else None —
    silently, since a missing phone/consent isn't an error, just nothing
    to send to."""
    patient = get_record("patients", str(appointment.get("patient_id", "")))
    if not patient:
        return None
    if not bool(patient.get("consent_to_contact", False)):
        return None
    if not str(patient.get("phone", "")).strip():
        return None
    return patient


def _send(phone: str, body: str, log_summary: str) -> dict[str, Any]:
    result = _whatsapp_client().send_text({"to": phone, "body": body})
    mode = "simulated" if result.status == "simulated" else result.status
    _log(f"{log_summary} ({mode})", "Completed" if result.success else "Failed")
    return {
        "ok": result.success,
        "status": result.status,
        "detail": result.detail,
        "dry_run": result.status == "simulated",
    }


def send_appointment_confirmation(appointment: dict[str, Any]) -> dict[str, Any] | None:
    """Send an immediate WhatsApp confirmation for a just-booked appointment."""
    patient = _patient_for_appointment(appointment)
    if patient is None:
        return None

    name = patient.get("name") or "there"
    clinic = _clinic_name()
    appt_date = appointment.get("appointment_date", "")
    appt_time = appointment.get("appointment_time", "")
    service = appointment.get("service") or "your session"
    body = (
        f"Hi {name}, your {service} appointment at {clinic} is confirmed for "
        f"{appt_date} at {appt_time}. See you then!"
    )
    return _send(
        patient["phone"],
        body,
        f"Sent booking confirmation to {name} for {appt_date} {appt_time}",
    )


def send_appointment_rsvp_reminder(appointment: dict[str, Any]) -> dict[str, Any] | None:
    """Send the 24-hours-before RSVP reminder for a Scheduled appointment.

    Asks the patient to confirm or flag a reschedule, rather than just
    stating the time — this is what distinguishes it from a plain
    reminder per the founder's request."""
    patient = _patient_for_appointment(appointment)
    if patient is None:
        return None

    name = patient.get("name") or "there"
    clinic = _clinic_name()
    appt_date = appointment.get("appointment_date", "")
    appt_time = appointment.get("appointment_time", "")
    service = appointment.get("service") or "your session"
    body = (
        f"Hi {name}, this is {clinic}. Reminder: your {service} appointment "
        f"is tomorrow, {appt_date} at {appt_time}. Reply YES to confirm, or "
        f"let us know if you need to reschedule."
    )
    return _send(
        patient["phone"],
        body,
        f"Sent 24hr RSVP reminder to {name} for {appt_date} {appt_time}",
    )
