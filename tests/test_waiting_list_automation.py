"""Regression test for Phase 1's seventh automation: waiting_list_automation.

Runs entirely against a temporary local store and a temporary clinic-data
directory, never the real database or the real data/pilot/*.json files.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import date, timedelta
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import scheduler.run_scheduled_checks as scheduler


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_base = clinic_data.BASE
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"
            clinic_data.BASE = root / "pilot"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.waiting_list_automation)

            today = date.today()
            clinic_data.add_record("therapists", {
                "therapist_id": "T-001", "name": "Dr Test", "status": "Active", "weekly_capacity": 10,
            })
            clinic_data.add_record("patients", {"patient_id": "P-001", "name": "Sample"})

            # --- no cancellations: nothing flagged -------------------------
            clinic_data.save_records("appointments", [
                {
                    "appointment_id": "A-001", "patient_id": "P-001", "therapist_id": "T-001",
                    "appointment_date": (today + timedelta(days=1)).isoformat(),
                    "appointment_time": "10:00", "status": "Scheduled",
                },
            ])
            result_none = scheduler.waiting_list_automation()
            assert result_none.alerts_raised == 0
            assert result_none.detail == "no cancelled future appointments"

            # --- a future cancellation: flagged once ------------------------
            clinic_data.save_records("appointments", [
                {
                    "appointment_id": "A-001", "patient_id": "P-001", "therapist_id": "T-001",
                    "appointment_date": (today + timedelta(days=1)).isoformat(),
                    "appointment_time": "10:00", "status": "Scheduled",
                },
                {
                    "appointment_id": "A-002", "patient_id": "P-001", "therapist_id": "T-001",
                    "appointment_date": (today + timedelta(days=2)).isoformat(),
                    "appointment_time": "14:00", "status": "Cancelled",
                },
                {
                    # cancelled but in the PAST: should not be flagged (slot is moot)
                    "appointment_id": "A-003", "patient_id": "P-001", "therapist_id": "T-001",
                    "appointment_date": (today - timedelta(days=5)).isoformat(),
                    "appointment_time": "09:00", "status": "Cancelled",
                },
            ])
            result = scheduler.waiting_list_automation()
            assert result.alerts_raised == 1, f"expected exactly the future cancellation flagged, got {result}"
            assert result.skipped_duplicate == 0

            reports = business_memory.get_memory_section("reports")
            hits = [r for r in reports if r.get("data", {}).get("check") == "waiting_list_automation"]
            assert len(hits) == 1
            assert "Dr Test" in hits[0]["data"]["message"]
            assert hits[0]["data"]["type"] == "Risk"

            # --- run again: the same cancellation must not re-fire ---------
            result_again = scheduler.waiting_list_automation()
            assert result_again.alerts_raised == 0
            assert result_again.skipped_duplicate == 1

            reports_after = business_memory.get_memory_section("reports")
            hits_after = [
                r for r in reports_after if r.get("data", {}).get("check") == "waiting_list_automation"
            ]
            assert len(hits_after) == 1, "a specific cancellation must only ever be flagged once"

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        clinic_data.BASE = original_base
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Waiting list automation tests passed.")
