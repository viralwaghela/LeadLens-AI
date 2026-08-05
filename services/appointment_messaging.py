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


def send_appointment_confirmation(appointment: dict[str, Any]) -> dict[str, Any] | None:
    """Send an immediate WhatsApp confirmation for a just-booked appointment.

    Returns the WhatsApp send result dict, or None if skipped (no consent
    or no phone on file) — silently, since a missing phone/consent isn't
    an error, just nothing to send to.
    """
    patient = get_record("patients", str(appointment.get("patient_id", "")))
    if not patient:
        return None
    if not bool(patient.get("consent_to_contact", False)):
        return None
    phone = str(patient.get("phone", "")).strip()
    if not phone:
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

    result = WhatsAppBusinessService().send_text({"to": phone, "body": body})
    mode = "simulated" if result.status == "simulated" else result.status
    _log(
        f"Sent booking confirmation to {name} for {appt_date} {appt_time} ({mode})",
        "Completed" if result.success else "Failed",
    )
    return {
        "ok": result.success,
        "status": result.status,
        "detail": result.detail,
        "dry_run": result.status == "simulated",
    }
