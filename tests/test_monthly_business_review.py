"""Regression test for Phase 1's fourth automation: monthly_business_review.

Runs entirely against a temporary local store, never the real database.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import date
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import scheduler.run_scheduled_checks as scheduler


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.monthly_business_review)

            clinic_data.add_record("patients", {"patient_id": "P-001", "name": "Sample", "status": "Active"})
            clinic_data.add_record(
                "therapists", {"therapist_id": "T-001", "name": "Dr Test", "status": "Active", "weekly_capacity": 10}
            )

            # --- first run this month: fires ------------------------------
            result = scheduler.monthly_business_review()
            assert result.alerts_raised == 1
            assert result.skipped_duplicate == 0

            reports = business_memory.get_memory_section("reports")
            hits = [r for r in reports if r.get("data", {}).get("check") == "monthly_business_review"]
            assert len(hits) == 1
            assert hits[0]["data"]["type"] == "Info", "should be Info, not Risk — this isn't a problem alert"
            assert date.today().strftime("%B %Y") in hits[0]["data"]["title"]
            assert "Active patients: 1/1" in hits[0]["data"]["message"]

            # --- same month, run again: must not duplicate ----------------
            result_again = scheduler.monthly_business_review()
            assert result_again.alerts_raised == 0
            assert result_again.skipped_duplicate == 1

            reports_after = business_memory.get_memory_section("reports")
            hits_after = [r for r in reports_after if r.get("data", {}).get("check") == "monthly_business_review"]
            assert len(hits_after) == 1, "must not fire twice in the same month"

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Monthly business review tests passed.")
