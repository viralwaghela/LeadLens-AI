"""Regression test for Phase 1's second automation: capacity_alert.

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
            scheduler.CHECKS.append(scheduler.capacity_alert)

            today = date.today()

            # --- one therapist over capacity, one within it --------------
            over_booked = [
                {
                    "appointment_id": f"A-{i:03d}",
                    "patient_id": "P-001",
                    "therapist_id": "T-001",
                    "appointment_date": (today + timedelta(days=i % 7)).isoformat(),
                    "status": "Scheduled",
                }
                for i in range(12)
            ]
            within_capacity = [
                {
                    "appointment_id": f"B-{i:03d}",
                    "patient_id": "P-001",
                    "therapist_id": "T-002",
                    "appointment_date": (today + timedelta(days=i % 7)).isoformat(),
                    "status": "Scheduled",
                }
                for i in range(3)
            ]
            _seed(
                therapists=[
                    {"therapist_id": "T-001", "name": "Dr Over", "status": "Active", "weekly_capacity": 10},
                    {"therapist_id": "T-002", "name": "Dr Fine", "status": "Active", "weekly_capacity": 10},
                ],
                appointments=over_booked + within_capacity,
            )

            result = scheduler.capacity_alert()
            assert result.alerts_raised == 1, "expected exactly one over-capacity therapist flagged"
            assert result.skipped_duplicate == 0

            reports = business_memory.get_memory_section("reports")
            hits = [r for r in reports if r.get("data", {}).get("check") == "capacity_alert"]
            assert len(hits) == 1
            assert "Dr Over" in hits[0]["data"]["title"]
            assert hits[0]["data"]["type"] == "Risk"

            # --- same day, still over capacity: must not duplicate -------
            result_again = scheduler.capacity_alert()
            assert result_again.alerts_raised == 0
            assert result_again.skipped_duplicate == 1

            reports_after = business_memory.get_memory_section("reports")
            hits_after = [r for r in reports_after if r.get("data", {}).get("check") == "capacity_alert"]
            assert len(hits_after) == 1, "must not re-alert the same therapist same day"

            # --- inactive therapist over booked appointments: ignored ----
            business_memory.DATABASE_FOLDER = root / "database2"
            _seed(
                therapists=[
                    {"therapist_id": "T-003", "name": "Dr Inactive", "status": "Inactive", "weekly_capacity": 5},
                ],
                appointments=[
                    {
                        "appointment_id": f"C-{i:03d}",
                        "patient_id": "P-001",
                        "therapist_id": "T-003",
                        "appointment_date": today.isoformat(),
                        "status": "Scheduled",
                    }
                    for i in range(10)
                ],
            )
            result_inactive = scheduler.capacity_alert()
            assert result_inactive.alerts_raised == 0
            assert result_inactive.detail == "no therapists over capacity"

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Capacity alert tests passed.")
