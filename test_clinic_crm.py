from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import services.clinic_data_service as crm
from services.live_workflow_service import due_followups


def run_tests():
    original_base = crm.BASE
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            crm.BASE = Path(temp_dir)

            patient = crm.add_record(
                "patients",
                {
                    "name": "Demo Patient",
                    "phone": "9999999999",
                    "email": "demo@example.com",
                    "status": "Active",
                    "consent_to_contact": False,
                    "sessions_remaining": 0,
                },
            )
            assert patient["patient_id"] == "P-001"

            therapist = crm.add_record(
                "therapists",
                {
                    "name": "Demo Therapist",
                    "status": "Active",
                    "weekly_capacity": 35,
                },
            )
            assert therapist["therapist_id"] == "T-001"

            old_date = (date.today() - timedelta(days=40)).isoformat()
            appointment = crm.add_record(
                "appointments",
                {
                    "patient_id": patient["patient_id"],
                    "therapist_id": therapist["therapist_id"],
                    "appointment_date": old_date,
                    "appointment_time": "10:00",
                    "service": "Physiotherapy",
                    "status": "Completed",
                },
            )
            assert appointment["appointment_id"] == "A-001"

            package = crm.add_record(
                "packages",
                {
                    "patient_id": patient["patient_id"],
                    "name": "Recovery plan",
                    "total_sessions": 10,
                    "sessions_remaining": 1,
                    "start_date": old_date,
                    "expiry_date": "",
                    "status": "Active",
                },
            )
            assert package["package_id"] == "PKG-001"

            payment = crm.add_record(
                "payments",
                {
                    "patient_id": patient["patient_id"],
                    "package_id": package["package_id"],
                    "amount": 12000,
                    "payment_date": date.today().isoformat(),
                    "method": "UPI",
                    "status": "Paid",
                },
            )
            assert payment["payment_id"] == "PAY-001"

            progress = crm.add_record(
                "progress_notes",
                {
                    "patient_id": patient["patient_id"],
                    "therapist_id": therapist["therapist_id"],
                    "visit_date": date.today().isoformat(),
                    "progress_status": "Improving",
                    "pain_score": 3,
                    "progress_summary": "Mobility improved since the last visit.",
                    "next_step": "Continue the current plan.",
                },
            )
            assert progress["progress_id"] == "PRG-001"

            profile = crm.patient_profile(patient["patient_id"])
            assert profile["last_visit"] == old_date
            assert profile["sessions_remaining"] == 1
            assert profile["payments_total"] == 12000
            assert profile["risk_level"] == "High"
            assert profile["latest_progress"]["progress_id"] == "PRG-001"
            assert profile["latest_progress"]["pain_score"] == 3
            assert "Package renewal due" in profile["risk_flags"]
            assert "Contact consent not recorded" in profile["risk_flags"]

            crm.update_record(
                "patients",
                patient["patient_id"],
                {"consent_to_contact": True, "status": "Renewal Due"},
            )
            updated = crm.get_record("patients", patient["patient_id"])
            assert updated and updated["consent_to_contact"] is True
            assert updated["status"] == "Renewal Due"
            renewal_candidates = due_followups("Package renewal")
            assert [row["patient_id"] for row in renewal_candidates] == ["P-001"]
            inactive_candidates = due_followups("Inactive patient recovery")
            assert [row["patient_id"] for row in inactive_candidates] == ["P-001"]

            future_date = (date.today() + timedelta(days=2)).isoformat()
            crm.add_record(
                "appointments",
                {
                    "patient_id": patient["patient_id"],
                    "therapist_id": therapist["therapist_id"],
                    "appointment_date": future_date,
                    "appointment_time": "11:00",
                    "service": "Physiotherapy",
                    "status": "Scheduled",
                },
            )
            reminder_candidates = due_followups("Appointment reminders")
            assert [row["patient_id"] for row in reminder_candidates] == ["P-001"]

            second = crm.add_record(
                "patients",
                {
                    "name": "Second Patient",
                    "status": "Active",
                    "consent_to_contact": True,
                },
            )
            assert second["patient_id"] == "P-002"
            crm.archive_record("patients", second["patient_id"])
            assert crm.get_record("patients", second["patient_id"]) is None
            assert (
                crm.get_record(
                    "patients",
                    second["patient_id"],
                    include_archived=True,
                )
                is not None
            )

            third = crm.add_record(
                "patients",
                {
                    "name": "Third Patient",
                    "status": "Active",
                    "consent_to_contact": True,
                },
            )
            assert third["patient_id"] == "P-003"

            try:
                crm.add_record(
                    "appointments",
                    {
                        "patient_id": "P-999",
                        "appointment_date": date.today().isoformat(),
                        "status": "Scheduled",
                    },
                )
            except ValueError as error:
                assert "valid patient" in str(error).lower()
            else:
                raise AssertionError("Invalid patient relationship was accepted.")

            try:
                crm.add_record(
                    "payments",
                    {
                        "patient_id": patient["patient_id"],
                        "amount": -1,
                        "payment_date": date.today().isoformat(),
                        "status": "Paid",
                    },
                )
            except ValueError:
                pass
            else:
                raise AssertionError("Negative payment was accepted.")

            metrics = crm.clinic_metrics()
            assert metrics["patients"] == 2
            assert metrics["payments_total"] == 12000
            assert metrics["renewals_due"] == 1
            assert metrics["upcoming_appointments"] == 1
    finally:
        crm.BASE = original_base


if __name__ == "__main__":
    run_tests()
    print("Clinic CRM tests passed.")
