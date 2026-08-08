"""Regression test for Phase 2's third automation: missed_appointment_recovery.

Tier 2 (patient-facing) — must only ever queue into the Approval Queue,
never send directly. Runs entirely against a temporary local store, never
the real database or real execution queue.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import services.integration_manager_v21 as manager
import scheduler.run_scheduled_checks as scheduler


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_queue = manager.QUEUE
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"
            manager.QUEUE = root / "execution_queue.json"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.missed_appointment_recovery)

            clinic_data.add_record("patients", {
                "patient_id": "P-001", "name": "Consented Patient", "phone": "919999999901",
                "consent_to_contact": True,
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-002", "name": "No Consent", "phone": "919999999902",
                "consent_to_contact": False,
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-003", "name": "No Phone", "consent_to_contact": True,
            })

            clinic_data.save_records("appointments", [
                {"appointment_id": "A-NOSHOW", "patient_id": "P-001",
                 "appointment_date": "2026-01-01", "appointment_time": "10:00",
                 "status": "No-show"},
                {"appointment_id": "A-COMPLETED", "patient_id": "P-001",
                 "appointment_date": "2026-01-01", "appointment_time": "10:00",
                 "status": "Completed"},  # attended fine, must not be flagged
                {"appointment_id": "A-SCHEDULED-PAST", "patient_id": "P-001",
                 "appointment_date": "2020-01-01", "appointment_time": "10:00",
                 "status": "Scheduled"},  # stale record, not a confirmed no-show
                {"appointment_id": "A-NOCONSENT", "patient_id": "P-002",
                 "appointment_date": "2026-01-01", "appointment_time": "10:00",
                 "status": "No-show"},
                {"appointment_id": "A-NOPHONE", "patient_id": "P-003",
                 "appointment_date": "2026-01-01", "appointment_time": "10:00",
                 "status": "No-show"},
            ])

            result = scheduler.missed_appointment_recovery()
            assert result.approvals_queued == 1, f"expected exactly A-NOSHOW queued, got {result}"
            assert result.skipped_duplicate == 0
            assert "2 skipped (no consent or phone on file)" in result.detail

            rows = manager.execution_rows()
            assert len(rows) == 1
            assert rows[0]["provider"] == "whatsapp"
            assert rows[0]["status"] == "Awaiting approval", "reschedule offers must never auto-send"
            assert rows[0]["payload"]["to"] == "919999999901"

            # --- run again: must not duplicate ------------------------------
            result_again = scheduler.missed_appointment_recovery()
            assert result_again.approvals_queued == 0
            assert result_again.skipped_duplicate == 1
            assert len(manager.execution_rows()) == 1

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        manager.QUEUE = original_queue
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Missed appointment recovery tests passed.")
