"""Regression test for Phase 2's first automation: birthday_automation.

Tier 2 (patient-facing) — must only ever queue into the Approval Queue,
never send directly. Runs entirely against a temporary local store, never
the real database or real execution queue.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import date, timedelta
from pathlib import Path

import core.memory as business_memory
import services.clinic_data_service as clinic_data
import services.integration_manager_v21 as manager
import scheduler.run_scheduled_checks as scheduler


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_checks = list(scheduler.CHECKS)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.birthday_automation)

            today = date.today()
            not_today = today - timedelta(days=30)

            clinic_data.add_record("patients", {
                "patient_id": "P-001", "name": "Birthday Patient", "phone": "919999999901",
                "consent_to_contact": True,
                "date_of_birth": date(1990, today.month, today.day).isoformat(),
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-002", "name": "Not Today", "phone": "919999999902",
                "consent_to_contact": True,
                "date_of_birth": date(1990, not_today.month, not_today.day).isoformat(),
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-003", "name": "No Consent", "phone": "919999999903",
                "consent_to_contact": False,
                "date_of_birth": date(1990, today.month, today.day).isoformat(),
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-004", "name": "No Phone",
                "consent_to_contact": True,
                "date_of_birth": date(1990, today.month, today.day).isoformat(),
            })
            clinic_data.add_record("patients", {
                "patient_id": "P-005", "name": "No DOB Recorded",
                "phone": "919999999905", "consent_to_contact": True,
            })

            result = scheduler.birthday_automation()
            assert result.approvals_queued == 1, f"expected exactly P-001 queued, got {result}"
            assert result.skipped_duplicate == 0

            rows = manager.execution_rows()
            assert len(rows) == 1, "only the birthday-today, consented, phoned patient should be queued"
            assert rows[0]["provider"] == "whatsapp"
            assert rows[0]["status"] == "Awaiting approval", "birthday messages must never auto-send"
            assert rows[0]["payload"]["to"] == "919999999901"
            assert "Birthday Patient" in rows[0]["payload"]["body"]

            # --- same patients, run again same day: must not duplicate -----
            result_again = scheduler.birthday_automation()
            assert result_again.approvals_queued == 0
            assert result_again.skipped_duplicate == 1
            assert len(manager.execution_rows()) == 1, "must not queue a duplicate birthday message"

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Birthday automation tests passed.")
