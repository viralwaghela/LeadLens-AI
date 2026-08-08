"""Regression test for Phase 2's sixth automation: corporate_lead_automation.

Research + draft only, never auto-send — every queued item must be a
Gmail draft (provider="gmail", action="create_draft"), never a
directly-sendable action. Runs entirely against a temporary local store,
never the real database or real execution queue.
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

            business_memory.update_memory(lambda mem: mem.update({
                "company": {"business_name": "Beyond Pain"},
            }))

            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(scheduler.corporate_lead_automation)

            clinic_data.add_record("corporate_clients", {
                "company_name": "Acme Corp", "contact_name": "Priya",
                "email": "priya@acme.example", "status": "New",
            })
            clinic_data.add_record("corporate_clients", {
                "company_name": "No Email Co", "status": "New",
            })
            clinic_data.add_record("corporate_clients", {
                "company_name": "Already Contacted Co", "email": "x@example.com",
                "status": "Contacted",
            })

            result = scheduler.corporate_lead_automation()
            assert result.approvals_queued == 1, f"expected exactly Acme Corp drafted, got {result}"
            assert result.skipped_duplicate == 0

            rows = manager.execution_rows()
            assert len(rows) == 1
            assert rows[0]["provider"] == "gmail"
            assert rows[0]["action"] == "create_draft", "must only ever create a draft, never send"
            assert rows[0]["status"] == "Awaiting approval"
            assert rows[0]["payload"]["to"] == "priya@acme.example"
            assert "Acme Corp" in rows[0]["payload"]["body"]
            assert "Beyond Pain" in rows[0]["payload"]["subject"]

            # --- run again: must not duplicate ------------------------------
            result_again = scheduler.corporate_lead_automation()
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
    print("Corporate lead automation tests passed.")
