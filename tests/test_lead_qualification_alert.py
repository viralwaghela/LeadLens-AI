"""Regression test for Phase 1's fifth automation: lead_qualification_alert.

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
            scheduler.CHECKS.append(scheduler.lead_qualification_alert)

            today = date.today()
            stale_date = (today - timedelta(days=10)).isoformat()
            fresh_date = today.isoformat()

            # --- no leads at all: no crash, no alert ----------------------
            clinic_data.save_records("leads", [])
            result_empty = scheduler.lead_qualification_alert()
            assert result_empty.alerts_raised == 0
            assert result_empty.detail == "no open leads"

            # --- mixed leads: one stale, one missing contact, one fine,
            #     one closed (should be ignored entirely) ------------------
            clinic_data.save_records("leads", [
                {"lead_id": "L-001", "status": "new", "phone": "9999999999", "created_at": stale_date},
                {"lead_id": "L-002", "status": "contacted", "created_at": fresh_date},  # missing contact
                {"lead_id": "L-003", "status": "new", "email": "a@b.com", "created_at": fresh_date},  # fine
                {"lead_id": "L-004", "status": "Converted", "created_at": stale_date},  # closed, ignored
            ])

            result = scheduler.lead_qualification_alert()
            assert result.alerts_raised == 1, f"expected an alert, got {result}"
            assert "3 open lead(s)" in result.detail
            assert "1 stale" in result.detail
            assert "1 missing contact info" in result.detail

            reports = business_memory.get_memory_section("reports")
            hits = [r for r in reports if r.get("data", {}).get("check") == "lead_qualification_alert"]
            assert len(hits) == 1
            assert hits[0]["data"]["type"] == "Risk"

            # --- same day, same situation: must not duplicate -------------
            result_again = scheduler.lead_qualification_alert()
            assert result_again.alerts_raised == 0
            assert result_again.skipped_duplicate == 1

            reports_after = business_memory.get_memory_section("reports")
            hits_after = [
                r for r in reports_after if r.get("data", {}).get("check") == "lead_qualification_alert"
            ]
            assert len(hits_after) == 1, "must not re-alert same day"

            # --- all open leads healthy (fresh + has contact): no alert ---
            business_memory.DATABASE_FOLDER = root / "database2"
            clinic_data.BASE = root / "pilot2"
            clinic_data.save_records("leads", [
                {"lead_id": "L-005", "status": "new", "phone": "8888888888", "created_at": fresh_date},
            ])
            result_healthy = scheduler.lead_qualification_alert()
            assert result_healthy.alerts_raised == 0
            reports_healthy = business_memory.get_memory_section("reports")
            assert not any(
                r.get("data", {}).get("check") == "lead_qualification_alert" for r in reports_healthy
            ), "must not alert when open leads are fresh and have contact info"

            # --- lead with no date field at all: counted open, not stale --
            business_memory.DATABASE_FOLDER = root / "database3"
            clinic_data.BASE = root / "pilot3"
            clinic_data.save_records("leads", [
                {"lead_id": "L-006", "status": "new", "phone": "7777777777"},
            ])
            result_no_date = scheduler.lead_qualification_alert()
            assert result_no_date.alerts_raised == 0
            assert "1 open lead(s), 0 stale" in result_no_date.detail

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        clinic_data.BASE = original_base
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Lead qualification alert tests passed.")
