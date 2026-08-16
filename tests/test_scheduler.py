"""Regression tests for the scheduler foundation (Automation Roadmap Phase 0).

Runs entirely against a temporary local store, never the real database, so
it's safe to run repeatedly without touching production data.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import os
import tempfile
from pathlib import Path

import core.memory as business_memory
import services.integration_manager_v21 as manager
import scheduler.run_scheduled_checks as scheduler


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_checks = list(scheduler.CHECKS)
    original_database_url = os.environ.pop("DATABASE_URL", None)

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"
            scheduler.CHECKS.clear()

            # --- registry -----------------------------------------------
            calls = []

            @scheduler.check
            def sample_check(context=None):
                calls.append(1)
                return scheduler.CheckResult(alerts_raised=1)

            assert sample_check in scheduler.CHECKS
            results = scheduler.run_all_checks()
            assert len(calls) == 1
            assert results["sample_check"].alerts_raised == 1

            # --- a broken check must not take down the others -----------
            @scheduler.check
            def broken_check(context=None):
                raise RuntimeError("boom")

            @scheduler.check
            def other_check(context=None):
                calls.append(2)
                return scheduler.CheckResult()

            results = scheduler.run_all_checks()
            assert results["broken_check"].detail.startswith("FAILED")
            assert results["other_check"].detail == ""
            assert 2 in calls

            # --- owner-facing alert: idempotent per (check, item_key) ---
            raised_first = scheduler.raise_owner_alert(
                "renewal_check", "P-100:2026-08-01",
                title="Package renewal due",
                message="Patient P-100 has 0 sessions remaining.",
            )
            assert raised_first is True

            raised_second = scheduler.raise_owner_alert(
                "renewal_check", "P-100:2026-08-01",
                title="Package renewal due",
                message="Patient P-100 has 0 sessions remaining.",
            )
            assert raised_second is False

            risk_reports = [
                r for r in business_memory.get_memory_section("reports")
                if r.get("data", {}).get("type") == "Risk"
                and r.get("data", {}).get("check") == "renewal_check"
            ]
            assert len(risk_reports) == 1

            # A different item_key for the same check must still alert.
            raised_different_patient = scheduler.raise_owner_alert(
                "renewal_check", "P-101:2026-08-01",
                title="Package renewal due",
                message="Patient P-101 has 0 sessions remaining.",
            )
            assert raised_different_patient is True

            # --- patient-facing action: routed through the approval queue,
            #     idempotent the same way, never auto-sent ----------------
            queued_first = scheduler.queue_patient_action(
                "reminder_check", "P-200:2026-08-01",
                provider="whatsapp",
                action="send_text",
                payload={"to": "919999999999", "body": "See you tomorrow!"},
                title="Prepare appointment reminder",
            )
            assert queued_first is not None
            assert queued_first["status"] == "Awaiting approval"

            queued_second = scheduler.queue_patient_action(
                "reminder_check", "P-200:2026-08-01",
                provider="whatsapp",
                action="send_text",
                payload={"to": "919999999999", "body": "See you tomorrow!"},
                title="Prepare appointment reminder",
            )
            assert queued_second is None

            rows = manager.execution_rows()
            assert len(rows) == 1
            assert rows[0]["status"] == "Awaiting approval"

            # --- run log ---------------------------------------------------
            scheduler.CHECKS.clear()
            scheduler.CHECKS.append(sample_check)
            run_results = scheduler.run_all_checks()
            scheduler._log_run(run_results)
            run_log = business_memory.get_memory_section(scheduler.RUN_LOG_SECTION)
            assert len(run_log) == 1
            assert run_log[0]["data"]["checks_run"] == 1
            assert run_log[0]["data"]["checks_failed"] == 0

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        scheduler.CHECKS.clear()
        scheduler.CHECKS.extend(original_checks)
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url


if __name__ == "__main__":
    run_tests()
    print("Scheduler foundation tests passed.")
