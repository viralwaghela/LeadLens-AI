"""Regression test for Phase 1's third automation: revenue_monitoring.

Runs entirely against a temporary local store, never the real database.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import calendar
import tempfile
from datetime import date
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import scheduler.run_scheduled_checks as scheduler


def _month_bounds():
    today = date.today()
    this_month_start = today.replace(day=1)
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)
    last_month_days = calendar.monthrange(last_month_start.year, last_month_start.month)[1]
    checkpoint_day = min(today.day, last_month_days)
    last_month_checkpoint = last_month_start.replace(day=checkpoint_day)
    return today, this_month_start, last_month_start, last_month_checkpoint


def _payment(payment_id, patient_id, amount, payment_date, status="Paid"):
    return {
        "payment_id": payment_id,
        "patient_id": patient_id,
        "amount": amount,
        "payment_date": payment_date.isoformat(),
        "status": status,
    }


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.revenue_monitoring)

            today, this_month_start, last_month_start, last_month_checkpoint = _month_bounds()
            clinic_data.add_record("patients", {"patient_id": "P-001", "name": "Sample"})

            # --- behind pace: last month 1000 at checkpoint, this month 300 ---
            clinic_data.save_records("payments", [
                _payment("PAY-001", "P-001", 1000, last_month_checkpoint),
                _payment("PAY-002", "P-001", 300, today),
            ])

            result = scheduler.revenue_monitoring()
            assert result.alerts_raised == 1, f"expected an alert for behind-pace revenue, got {result}"
            assert result.skipped_duplicate == 0

            reports = business_memory.get_memory_section("reports")
            hits = [r for r in reports if r.get("data", {}).get("check") == "revenue_monitoring"]
            assert len(hits) == 1
            assert hits[0]["data"]["type"] == "Risk"

            # --- same day, still behind: must not duplicate ------------------
            result_again = scheduler.revenue_monitoring()
            assert result_again.alerts_raised == 0
            assert result_again.skipped_duplicate == 1

            reports_after = business_memory.get_memory_section("reports")
            hits_after = [r for r in reports_after if r.get("data", {}).get("check") == "revenue_monitoring"]
            assert len(hits_after) == 1, "must not re-alert same day"

            # --- healthy pace: no alert ----------------------------------------
            business_memory.DATABASE_FOLDER = root / "database2"
            clinic_data.add_record("patients", {"patient_id": "P-001", "name": "Sample"})
            clinic_data.save_records("payments", [
                _payment("PAY-001", "P-001", 1000, last_month_checkpoint),
                _payment("PAY-002", "P-001", 900, today),
            ])
            result_healthy = scheduler.revenue_monitoring()
            assert result_healthy.alerts_raised == 0
            assert result_healthy.skipped_duplicate == 0

            reports_healthy = business_memory.get_memory_section("reports")
            assert not any(
                r.get("data", {}).get("check") == "revenue_monitoring" for r in reports_healthy
            ), "must not alert when pace is healthy"

            # --- no prior-month baseline: skip, don't crash --------------------
            business_memory.DATABASE_FOLDER = root / "database3"
            clinic_data.add_record("patients", {"patient_id": "P-001", "name": "Sample"})
            clinic_data.save_records("payments", [_payment("PAY-001", "P-001", 300, today)])
            result_no_baseline = scheduler.revenue_monitoring()
            assert result_no_baseline.alerts_raised == 0
            assert "no prior-month baseline" in result_no_baseline.detail

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Revenue monitoring tests passed.")
