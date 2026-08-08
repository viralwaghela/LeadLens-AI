"""Regression test for Phase 1's sixth automation: appointment_reminder.

The 24hr reminder auto-sends directly (founder-approved exception, see
services.appointment_messaging) and never touches the Approval Queue. The
2hr reminder is unchanged: it must still go through the Approval Queue
(services.integration_manager_v21), never send directly. Runs entirely
against a temporary local store, never the real database or real
execution queue.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import services.integration_manager_v21 as manager
import scheduler.run_scheduled_checks as scheduler


def _appointment(appointment_id, patient_id, when: datetime, status="Scheduled"):
    return {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "therapist_id": "T-001",
        "appointment_date": when.strftime("%Y-%m-%d"),
        "appointment_time": when.strftime("%H:%M"),
        "status": status,
        "service": "Physiotherapy",
    }


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.appointment_reminder)

            now = datetime.now()

            clinic_data.add_record("therapists", {
                "therapist_id": "T-001", "name": "Dr Test", "status": "Active", "weekly_capacity": 10,
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-001", "name": "Consented Patient", "phone": "919999999999",
                "consent_to_contact": True,
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-002", "name": "No Consent Patient", "phone": "918888888888",
                "consent_to_contact": False,
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-003", "name": "No Phone Patient", "consent_to_contact": True,
            })

            clinic_data.save_records("appointments", [
                _appointment("A-24H", "P-001", now + timedelta(hours=24)),   # in window
                _appointment("A-2H", "P-001", now + timedelta(hours=2)),     # in window
                _appointment("A-FAR", "P-001", now + timedelta(hours=72)),   # not in any window
                _appointment("A-PAST", "P-001", now - timedelta(hours=1)),   # already passed
                _appointment("A-NOCONSENT", "P-002", now + timedelta(hours=24)),
                _appointment("A-NOPHONE", "P-003", now + timedelta(hours=24)),
                _appointment("A-CANCELLED", "P-001", now + timedelta(hours=24), status="Cancelled"),
            ])

            result = scheduler.appointment_reminder()
            assert result.messages_sent == 1, f"expected 1 24hr RSVP auto-sent (P-001 only), got {result}"
            assert result.approvals_queued == 1, f"expected 1 2hr reminder queued (P-001 only), got {result}"
            assert result.skipped_duplicate == 0

            rows = manager.execution_rows()
            assert len(rows) == 1, "only the 2hr reminder should reach the Approval Queue"
            assert rows[0]["provider"] == "whatsapp"
            assert rows[0]["status"] == "Awaiting approval", "2hr reminder must never auto-send"
            assert rows[0]["payload"]["to"] == "919999999999"

            # --- same appointments, run again: must not duplicate ---------
            # All 3 patients in the 24hr window (sent, no-consent, no-phone)
            # get marked flagged on the first pass regardless of outcome, so
            # all 3 are duplicates now; plus the one already-queued 2hr item.
            result_again = scheduler.appointment_reminder()
            assert result_again.messages_sent == 0
            assert result_again.approvals_queued == 0
            assert result_again.skipped_duplicate == 4
            assert len(manager.execution_rows()) == 1, "must not queue duplicate reminders"

            # --- unparseable appointment_time: skipped, not crashed -------
            business_memory.DATABASE_FOLDER = root / "database2"
            clinic_data.add_record("patients", {
                "patient_id": "P-001", "name": "Test", "phone": "919999999999", "consent_to_contact": True,
            })
            bad_appointment = _appointment("A-BAD", "P-001", now + timedelta(hours=24))
            bad_appointment["appointment_time"] = "morning"
            clinic_data.save_records("appointments", [bad_appointment])
            result_bad = scheduler.appointment_reminder()
            assert result_bad.approvals_queued == 0
            assert "1 appointment(s) with unparseable time" in result_bad.detail

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Appointment reminder tests passed.")
