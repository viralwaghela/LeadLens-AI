"""Regression test for Phase 1's first automation: low_booking_alert.

Runs entirely against a temporary local store, never the real database.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import date, timedelta
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import scheduler.run_scheduled_checks as scheduler


def _seed(therapists, appointments):
    clinic_data.save_records("therapists", therapists)
    clinic_data.save_records("appointments", appointments)


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.low_booking_alert)

            today = date.today()

            # --- under-booked: 0 of 10 weekly slots booked -> alert -------
            _seed(
                therapists=[{"therapist_id": "T-001", "name": "Dr Test", "status": "Active", "weekly_capacity": 10}],
                appointments=[],
            )

            result = scheduler.low_booking_alert()
            assert result.alerts_raised == 1, "expected an alert for 0% utilization"
            assert result.skipped_duplicate == 0

            reports = business_memory.get_memory_section("reports")
            low_booking_reports = [
                r for r in reports if r.get("data", {}).get("check") == "low_booking_alert"
            ]
            assert len(low_booking_reports) == 1
            assert low_booking_reports[0]["data"]["type"] == "Risk"

            # --- same day, still under-booked: must not duplicate --------
            result_again = scheduler.low_booking_alert()
            assert result_again.alerts_raised == 0
            assert result_again.skipped_duplicate == 1

            reports = business_memory.get_memory_section("reports")
            low_booking_reports = [
                r for r in reports if r.get("data", {}).get("check") == "low_booking_alert"
            ]
            assert len(low_booking_reports) == 1, "must not re-alert same day"

            # --- healthy utilization: no alert ----------------------------
            business_memory.DATABASE_FOLDER = root / "database2"
            scheduled = [
                {
                    "appointment_id": f"A-{i:03d}",
                    "patient_id": "P-001",
                    "therapist_id": "T-001",
                    "appointment_date": (today + timedelta(days=i % 7)).isoformat(),
                    "status": "Scheduled",
                }
                for i in range(6)
            ]
            clinic_data.add_record("patients", {"patient_id": "P-001", "name": "Sample"})
            _seed(
                therapists=[{"therapist_id": "T-001", "name": "Dr Test", "status": "Active", "weekly_capacity": 10}],
                appointments=scheduled,
            )
            result_healthy = scheduler.low_booking_alert()
            assert result_healthy.alerts_raised == 0
            assert result_healthy.skipped_duplicate == 0
            assert "60%" in result_healthy.detail

            reports_after = business_memory.get_memory_section("reports")
            assert not any(
                r.get("data", {}).get("check") == "low_booking_alert" for r in reports_after
            ), "must not alert when utilization is healthy"

            # --- no active therapist capacity: skip, don't crash ---------
            business_memory.DATABASE_FOLDER = root / "database3"
            _seed(therapists=[], appointments=[])
            result_no_capacity = scheduler.low_booking_alert()
            assert result_no_capacity.alerts_raised == 0
            assert "skipped" in result_no_capacity.detail

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Low booking alert tests passed.")
