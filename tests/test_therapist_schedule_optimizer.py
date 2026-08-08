"""Regression test for Phase 2's seventh and final automation:
therapist_schedule_optimizer.

Tier 1 (owner-facing suggestion only) — must never touch an appointment
record, only raise an alert. Runs entirely against a temporary local
store, never the real database.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import date, timedelta
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import scheduler.run_scheduled_checks as scheduler


def _appt(appointment_id, therapist_id, days_ahead):
    return {
        "appointment_id": appointment_id,
        "patient_id": "P-001",
        "therapist_id": therapist_id,
        "appointment_date": (date.today() + timedelta(days=days_ahead)).isoformat(),
        "appointment_time": "10:00",
        "status": "Scheduled",
    }


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.therapist_schedule_optimizer)

            clinic_data.add_record("patients", {"patient_id": "P-001", "name": "Sample"})
            # Over-booked: capacity 2, but 3 appointments scheduled.
            clinic_data.add_record("therapists", {
                "therapist_id": "T-OVER", "name": "Dr Busy", "status": "Active", "weekly_capacity": 2,
            })
            # Spare capacity: capacity 5, only 1 appointment scheduled.
            clinic_data.add_record("therapists", {
                "therapist_id": "T-SPARE", "name": "Dr Free", "status": "Active", "weekly_capacity": 5,
            })
            # Inactive therapist with spare capacity must never be suggested.
            clinic_data.add_record("therapists", {
                "therapist_id": "T-INACTIVE", "name": "Dr Gone", "status": "Inactive", "weekly_capacity": 10,
            })

            clinic_data.save_records("appointments", [
                _appt("A-001", "T-OVER", 1),
                _appt("A-002", "T-OVER", 2),
                _appt("A-003", "T-OVER", 3),
                _appt("A-004", "T-SPARE", 1),
            ])

            result = scheduler.therapist_schedule_optimizer()
            assert result.alerts_raised == 1, f"expected one rebalancing suggestion, got {result}"
            assert result.skipped_duplicate == 0

            reports = business_memory.get_memory_section("reports")
            hits = [r for r in reports if r.get("data", {}).get("check") == "therapist_schedule_optimizer"]
            assert len(hits) == 1
            message = hits[0]["data"]["message"]
            assert "Dr Busy" in message
            assert "Dr Free" in message
            assert "Dr Gone" not in message, "an inactive therapist must never be suggested"
            assert "no appointment has been changed" in message

            # --- run again same day: must not re-fire -----------------------
            result_again = scheduler.therapist_schedule_optimizer()
            assert result_again.alerts_raised == 0
            assert result_again.skipped_duplicate == 1

            # --- balanced schedule: no suggestion ----------------------------
            clinic_data.save_records("appointments", [_appt("A-005", "T-OVER", 1)])
            result_balanced = scheduler.therapist_schedule_optimizer()
            assert result_balanced.alerts_raised == 0
            assert result_balanced.detail == "no rebalancing opportunity found"

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Therapist schedule optimizer tests passed.")
