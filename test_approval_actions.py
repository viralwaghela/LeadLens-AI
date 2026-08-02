"""Safety and traceability tests for approval-gated external actions."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import core.memory as business_memory
import services.integration_manager_v21 as manager
import services.jarvis_memory as jarvis_memory


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER
    original_company_file = business_memory.COMPANY_FILE
    original_queue = manager.QUEUE
    original_store = jarvis_memory.STORE
    protected_environment = {
        key: os.environ.pop(key, None)
        for key in (
            "GOOGLE_SERVICE_ACCOUNT_FILE",
            "GMAIL_DELEGATED_USER",
            "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID",
        )
    }

    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            business_memory.DATABASE_FOLDER = root / "database"
            business_memory.COMPANY_FILE = (
                business_memory.DATABASE_FOLDER / "company.json"
            )
            manager.QUEUE = root / "execution_queue.json"
            jarvis_memory.STORE = root / "learning_memory.json"

            tracked = jarvis_memory.track_recommendation(
                "How can we recover inactive patients?",
                "Prepare a carefully reviewed renewal email.",
                agents=["Customer Success Agent"],
                tags=["Patient recovery"],
            )

            try:
                manager.prepare_execution(
                    "gmail",
                    "create_draft",
                    {"to": "", "subject": "Renewal", "body": "Hello"},
                )
                raise AssertionError("Invalid payload was accepted.")
            except ValueError:
                pass

            prepared = manager.prepare_execution(
                "gmail",
                "create_draft",
                {
                    "to": "patient@example.com",
                    "subject": "Package renewal",
                    "body": "Would you like to review your package options?",
                },
                "Prepare package-renewal email",
                recommendation_id=tracked["id"],
                impact="Recover one inactive patient without auto-sending.",
            )
            duplicate = manager.prepare_execution(
                "gmail",
                "create_draft",
                {
                    "to": "patient@example.com",
                    "subject": "Package renewal",
                    "body": "Would you like to review your package options?",
                },
                "Prepare package-renewal email",
                recommendation_id=tracked["id"],
                impact="Recover one inactive patient without auto-sending.",
            )
            assert prepared["id"] == duplicate["id"]

            blocked = manager.execute_item(prepared["id"])
            assert blocked["success"] is False
            assert blocked["status"] == "blocked"

            approved = manager.decide_item(prepared["id"], "Approved")
            assert approved["status"] == "Approved"

            first_result = manager.execute_item(prepared["id"])
            assert first_result["success"] is True
            assert first_result["status"] == "simulated"

            second_result = manager.execute_item(prepared["id"])
            assert second_result == first_result

            rows = manager.execution_rows()
            assert len(rows) == 1
            assert rows[0]["status"] == "Simulated"
            assert rows[0]["recommendation_id"] == tracked["id"]

            learning = jarvis_memory.load_learning_memory()
            assert len(learning["executions"]) == 1
            assert (
                learning["executions"][0]["execution_id"]
                == prepared["id"]
            )

            rejected = manager.prepare_execution(
                "whatsapp",
                "send_text",
                {"to": "919999999999", "body": "Test reminder"},
                "Prepare test WhatsApp reminder",
            )
            manager.decide_item(rejected["id"], "Rejected")
            rejected_result = manager.execute_item(rejected["id"])
            assert rejected_result["success"] is False
            assert rejected_result["status"] == "blocked"

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder
        business_memory.COMPANY_FILE = original_company_file
        manager.QUEUE = original_queue
        jarvis_memory.STORE = original_store
        for key, value in protected_environment.items():
            if value is not None:
                os.environ[key] = value


if __name__ == "__main__":
    run_tests()
    print("Approval-gated action tests passed.")
