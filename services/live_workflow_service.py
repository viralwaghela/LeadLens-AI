from __future__ import annotations

from datetime import date, datetime

from core.memory import add_approval, add_memory_entry, add_task
from services.clinic_data_service import list_records, patient_profile


WORKFLOW_TEMPLATES = {
    "Inactive patient recovery": (
        "We noticed you have not visited recently. Would you like help "
        "scheduling your next session?"
    ),
    "Package renewal": (
        "Your current package is nearing completion. We can help you select "
        "the best continuation plan."
    ),
    "Appointment reminders": (
        "This is a reminder for your upcoming appointment with Beyond Pain."
    ),
}


def _consented_patients() -> list[dict]:
    return [
        patient
        for patient in list_records("patients")
        if bool(patient.get("consent_to_contact", False))
    ]


def due_followups(workflow: str = "Inactive patient recovery") -> list[dict]:
    """Return eligible CRM patients without sending or changing any record."""
    consented = _consented_patients()
    if workflow == "Appointment reminders":
        today = date.today().isoformat()
        patient_ids = {
            str(row.get("patient_id"))
            for row in list_records("appointments")
            if row.get("status") == "Scheduled"
            and str(row.get("appointment_date", "")) >= today
        }
        return [
            patient
            for patient in consented
            if str(patient.get("patient_id")) in patient_ids
        ]

    candidates = []
    for patient in consented:
        profile = patient_profile(str(patient.get("patient_id")))
        flags = profile["risk_flags"]
        if workflow == "Package renewal":
            eligible = "Package renewal due" in flags
        else:
            eligible = (
                "Inactive patient" in flags
                or any(
                    str(flag).startswith("No completed visit for")
                    for flag in flags
                )
            )
        if eligible:
            candidates.append(patient)
    return candidates


def prepare_clinic_workflow(workflow: str) -> dict:
    if workflow not in WORKFLOW_TEMPLATES:
        raise ValueError(f"Unsupported clinic workflow: {workflow}")

    candidates = due_followups(workflow)
    audience_count = len(candidates)
    message = WORKFLOW_TEMPLATES[workflow]

    return {
        "audience": audience_count,
        "message": message,
        "task": add_task(
            f"Review {workflow} for {audience_count} patient(s)",
            "Customer Success",
            "High",
        ),
        "approval": add_approval(
            f"Approve {workflow}",
            "Customer Success",
            "High",
        ),
        "draft": add_memory_entry(
            "reports",
            {
                "type": "Live Workflow Draft",
                "title": workflow,
                "audience_count": audience_count,
                "message": message,
                "status": "Awaiting approval",
                "prepared_at": datetime.now().isoformat(timespec="seconds"),
            },
        ),
        "external_action_executed": False,
    }
