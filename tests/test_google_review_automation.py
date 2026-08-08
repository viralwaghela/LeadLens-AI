"""Regression test for Phase 2's second automation: google_review_automation.

Tier 2 (patient-facing) — must only ever queue into the Approval Queue,
never send directly, and must be a no-op when no review link is
configured. Runs entirely against a temporary local store, never the
real database or real execution queue.
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


def _appointment(appointment_id, patient_id, days_ago, status="Completed"):
    return {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "appointment_date": (date.today() - timedelta(days=days_ago)).isoformat(),
        "appointment_time": "10:00",
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
            scheduler.CHECKS.append(scheduler.google_review_automation)

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
                _appointment("A-INWINDOW", "P-001", 2),       # 2 days ago: in window
                _appointment("A-TOOSOON", "P-001", 0),        # today: too soon
                _appointment("A-TOOLATE", "P-001", 10),       # 10 days ago: too late
                _appointment("A-SCHEDULED", "P-001", 2, status="Scheduled"),  # not completed
                _appointment("A-NOCONSENT", "P-002", 2),
                _appointment("A-NOPHONE", "P-003", 2),
            ])

            # --- no review link configured: deliberate no-op ---------------
            result_unconfigured = scheduler.google_review_automation()
            assert result_unconfigured.approvals_queued == 0
            assert "no google_review_link configured" in result_unconfigured.detail
            assert len(manager.execution_rows()) == 0

            # --- with a review link configured ------------------------------
            business_memory.update_memory(lambda mem: mem.update({
                "company": {"google_review_link": "https://g.page/r/example/review"},
            }))
            result = scheduler.google_review_automation()
            assert result.approvals_queued == 1, f"expected exactly A-INWINDOW queued, got {result}"

            rows = manager.execution_rows()
            assert len(rows) == 1
            assert rows[0]["provider"] == "whatsapp"
            assert rows[0]["status"] == "Awaiting approval", "review requests must never auto-send"
            assert rows[0]["payload"]["to"] == "919999999901"
            assert "g.page/r/example/review" in rows[0]["payload"]["body"]

            # --- run again: must not duplicate ------------------------------
            result_again = scheduler.google_review_automation()
            assert result_again.approvals_queued == 0
            assert result_again.skipped_duplicate == 1
            assert len(manager.execution_rows()) == 1

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)


if __name__ == "__main__":
    run_tests()
    print("Google review automation tests passed.")
