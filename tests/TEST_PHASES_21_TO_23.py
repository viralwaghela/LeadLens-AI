import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from services.integration_manager_v21 import prepare_execution, execution_rows
from services.learning_memory_v22 import record_learning_outcome, recommendation_context
from services.agent_collaboration_v23 import run_agent_council
from integrations.calendar_service import GoogleCalendarService
from integrations.gmail_service import GmailService
from integrations.whatsapp_service import WhatsAppBusinessService


def run():
    assert GoogleCalendarService(dry_run=True).create_event({"summary":"Test","start":"2026-07-20T10:00:00+05:30","end":"2026-07-20T11:00:00+05:30"}).success
    assert GmailService(dry_run=True).create_draft({"to":"test@example.com","subject":"Test","body":"Hello"}).success
    assert WhatsAppBusinessService(dry_run=True).send_text({"to":"919999999999","body":"Test"}).success
    prepared = prepare_execution("gmail", "create_draft", {"to":"test@example.com","subject":"Test","body":"Hello"}, "Automated test")
    assert prepared["status"] == "Awaiting approval"
    record_learning_outcome("Patient recovery", "WhatsApp", "Inactive patients", 10, 2, 4000, 100)
    assert recommendation_context()["outcome_count"] >= 1
    council = run_agent_council("What should we prioritise?")
    assert len(council["agents"]) == 5
    assert council["synthesis"]["decision"]
    print("Phases 21-23 tests passed")


if __name__ == "__main__":
    run()
