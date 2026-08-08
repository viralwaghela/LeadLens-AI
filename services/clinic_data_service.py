from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

from core.memory import load_memory, update_memory

# Second element of each tuple is the key this entity lives under in
# core.memory's business-memory store (SQLite locally, Postgres in
# production — see core/memory.py) rather than a filename: this used to be
# a set of local JSON files under data/pilot/, which meant every patient,
# appointment, etc. a real clinic entered was written only to the
# container's local disk and silently lost on the next redeploy/restart on
# any host with an ephemeral filesystem (Streamlit Cloud included). Moved
# onto the same durable store the rest of the app's data already uses.
ENTITY_META = {
    "patients": ("clinic_patients", "patient_id", "P"),
    "appointments": ("clinic_appointments", "appointment_id", "A"),
    "packages": ("clinic_packages", "package_id", "PKG"),
    "package_templates": ("clinic_package_templates", "template_id", "PKGT"),
    "payments": ("clinic_payments", "payment_id", "PAY"),
    "therapists": ("clinic_therapists", "therapist_id", "T"),
    "progress_notes": ("clinic_progress_notes", "progress_id", "PRG"),
    "leads": ("clinic_leads", "lead_id", "L"),
    "corporate_clients": ("clinic_corporate_clients", "client_id", "C"),
}

PATIENT_STATUSES = {"Active", "Inactive", "Renewal Due", "Archived"}
PACKAGE_TEMPLATE_STATUSES = {"Active", "Archived"}
APPOINTMENT_STATUSES = {
    "Scheduled",
    "Completed",
    "Cancelled",
    "No-show",
    "Archived",
}
PACKAGE_STATUSES = {"Active", "Completed", "Expired", "Paused", "Archived"}
PAYMENT_STATUSES = {"Paid", "Pending", "Refunded", "Archived"}
THERAPIST_STATUSES = {"Active", "Inactive", "Archived"}
CORPORATE_CLIENT_STATUSES = {
    "New",
    "Contacted",
    "Proposal Sent",
    "Won",
    "Lost",
    "Archived",
}
LEAD_STATUSES = {
    "New",
    "Contacted",
    "Booked",
    "Converted",
    "Lost",
    "Archived",
}
LEAD_SOURCES = {"Website", "Phone", "Walk-in", "Referral", "Other"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _memory_key(entity: str) -> str:
    if entity not in ENTITY_META:
        raise ValueError(f"Unsupported entity: {entity}")
    return ENTITY_META[entity][0]


def _read_rows(entity: str) -> list[dict[str, Any]]:
    value = load_memory().get(_memory_key(entity), [])
    if not isinstance(value, list):
        raise RuntimeError(f"{_memory_key(entity)} must contain a list.")
    return [row for row in value if isinstance(row, dict)]


def list_records(
    entity: str,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    rows = _read_rows(entity)
    if include_archived:
        return rows
    return [
        row
        for row in rows
        if str(row.get("status", "")).strip().lower() != "archived"
        and not bool(row.get("archived", False))
    ]


def save_records(entity: str, rows: Iterable[dict[str, Any]]) -> None:
    key = _memory_key(entity)
    rows_list = list(rows)

    def mutate(memory: dict[str, Any]) -> None:
        memory[key] = rows_list

    update_memory(mutate)


def _next_id(entity: str, rows: list[dict[str, Any]]) -> str:
    _, id_field, prefix = ENTITY_META[entity]
    used_numbers = []
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for row in rows:
        match = pattern.match(str(row.get(id_field, "")))
        if match:
            used_numbers.append(int(match.group(1)))
    return f"{prefix}-{max(used_numbers, default=0) + 1:03d}"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_date(value: Any, field_name: str, *, required: bool = False) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        if required:
            raise ValueError(f"{field_name} is required.")
        return ""
    try:
        date.fromisoformat(cleaned)
    except ValueError as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format.") from error
    return cleaned


def _non_negative_number(value: Any, field_name: str) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a number.") from error
    if number < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return number


def _require_link(entity: str, record_id: Any) -> str:
    cleaned = _clean_text(record_id)
    if not cleaned or get_record(entity, cleaned) is None:
        label = entity.replace("_", " ").rstrip("s")
        raise ValueError(f"A valid {label} is required.")
    return cleaned


def _validate_status(value: Any, allowed: set[str], default: str) -> str:
    cleaned = _clean_text(value) or default
    if cleaned not in allowed:
        raise ValueError(
            f"Unsupported status '{cleaned}'. Choose one of: "
            + ", ".join(sorted(allowed))
        )
    return cleaned


def _validate_record(
    entity: str,
    data: dict[str, Any],
    *,
    partial: bool = False,
) -> dict[str, Any]:
    row = dict(data)

    if entity == "patients":
        if not partial or "name" in row:
            row["name"] = _clean_text(row.get("name"))
            if not row["name"]:
                raise ValueError("Patient name is required.")
        if "email" in row:
            row["email"] = _clean_text(row.get("email"))
        if "phone" in row:
            row["phone"] = _clean_text(row.get("phone"))
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), PATIENT_STATUSES, "Active"
            )
        if "last_visit" in row:
            row["last_visit"] = _clean_date(row.get("last_visit"), "Last visit")
        if "date_of_birth" in row:
            row["date_of_birth"] = _clean_date(
                row.get("date_of_birth"), "Date of birth"
            )
        if "sessions_remaining" in row:
            row["sessions_remaining"] = int(
                _non_negative_number(
                    row.get("sessions_remaining"), "Sessions remaining"
                )
            )
        if "consent_to_contact" in row:
            row["consent_to_contact"] = bool(row.get("consent_to_contact"))

    elif entity == "therapists":
        if not partial or "name" in row:
            row["name"] = _clean_text(row.get("name"))
            if not row["name"]:
                raise ValueError("Therapist name is required.")
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), THERAPIST_STATUSES, "Active"
            )
        if "weekly_capacity" in row:
            row["weekly_capacity"] = int(
                _non_negative_number(
                    row.get("weekly_capacity"), "Weekly capacity"
                )
            )

    elif entity == "appointments":
        if not partial or "patient_id" in row:
            row["patient_id"] = _require_link("patients", row.get("patient_id"))
        if row.get("therapist_id"):
            row["therapist_id"] = _require_link(
                "therapists", row.get("therapist_id")
            )
        if not partial or "appointment_date" in row:
            row["appointment_date"] = _clean_date(
                row.get("appointment_date"),
                "Appointment date",
                required=True,
            )
        if "appointment_time" in row:
            row["appointment_time"] = _clean_text(row.get("appointment_time"))
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), APPOINTMENT_STATUSES, "Scheduled"
            )
        if "service" in row:
            row["service"] = _clean_text(row.get("service"))

    elif entity == "packages":
        if not partial or "patient_id" in row:
            row["patient_id"] = _require_link("patients", row.get("patient_id"))
        if not partial or "name" in row:
            row["name"] = _clean_text(row.get("name"))
            if not row["name"]:
                raise ValueError("Package name is required.")
        if "total_sessions" in row:
            row["total_sessions"] = int(
                _non_negative_number(row.get("total_sessions"), "Total sessions")
            )
        if "sessions_remaining" in row:
            row["sessions_remaining"] = int(
                _non_negative_number(
                    row.get("sessions_remaining"), "Sessions remaining"
                )
            )
        total = row.get("total_sessions")
        remaining = row.get("sessions_remaining")
        if total is not None and remaining is not None and remaining > total:
            raise ValueError("Sessions remaining cannot exceed total sessions.")
        if "start_date" in row:
            row["start_date"] = _clean_date(row.get("start_date"), "Start date")
        if "expiry_date" in row:
            row["expiry_date"] = _clean_date(row.get("expiry_date"), "Expiry date")
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), PACKAGE_STATUSES, "Active"
            )

    elif entity == "package_templates":
        if not partial or "name" in row:
            row["name"] = _clean_text(row.get("name"))
            if not row["name"]:
                raise ValueError("Package name is required.")
        if "total_sessions" in row:
            row["total_sessions"] = int(
                _non_negative_number(row.get("total_sessions"), "Total sessions")
            )
        if "price" in row:
            row["price"] = _non_negative_number(row.get("price"), "Price")
        if "description" in row:
            row["description"] = _clean_text(row.get("description"))
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), PACKAGE_TEMPLATE_STATUSES, "Active"
            )

    elif entity == "payments":
        if not partial or "patient_id" in row:
            row["patient_id"] = _require_link("patients", row.get("patient_id"))
        if row.get("package_id"):
            row["package_id"] = _require_link(
                "packages", row.get("package_id")
            )
        if not partial or "amount" in row:
            row["amount"] = _non_negative_number(row.get("amount"), "Amount")
            if row["amount"] <= 0:
                raise ValueError("Amount must be greater than zero.")
        if not partial or "payment_date" in row:
            row["payment_date"] = _clean_date(
                row.get("payment_date"), "Payment date", required=True
            )
        if "method" in row:
            row["method"] = _clean_text(row.get("method"))
        if "reference" in row:
            row["reference"] = _clean_text(row.get("reference"))
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), PAYMENT_STATUSES, "Paid"
            )

    elif entity == "progress_notes":
        if not partial or "patient_id" in row:
            row["patient_id"] = _require_link("patients", row.get("patient_id"))
        if row.get("therapist_id"):
            row["therapist_id"] = _require_link(
                "therapists", row.get("therapist_id")
            )
        if not partial or "visit_date" in row:
            row["visit_date"] = _clean_date(
                row.get("visit_date"),
                "Progress date",
                required=True,
            )
        if not partial or "progress_summary" in row:
            row["progress_summary"] = _clean_text(
                row.get("progress_summary")
            )
            if not row["progress_summary"]:
                raise ValueError("Progress summary is required.")
        if "pain_score" in row:
            score = int(_non_negative_number(row.get("pain_score"), "Pain score"))
            if score > 10:
                raise ValueError("Pain score cannot exceed 10.")
            row["pain_score"] = score
        if "progress_status" in row:
            row["progress_status"] = _clean_text(
                row.get("progress_status")
            )
        if "next_step" in row:
            row["next_step"] = _clean_text(row.get("next_step"))

    elif entity == "corporate_clients":
        if not partial or "company_name" in row:
            row["company_name"] = _clean_text(row.get("company_name"))
            if not row["company_name"]:
                raise ValueError("Company name is required.")
        if "contact_name" in row:
            row["contact_name"] = _clean_text(row.get("contact_name"))
        if "phone" in row:
            row["phone"] = _clean_text(row.get("phone"))
        if "email" in row:
            row["email"] = _clean_text(row.get("email"))
        if "notes" in row:
            row["notes"] = _clean_text(row.get("notes"))
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), CORPORATE_CLIENT_STATUSES, "New"
            )

    elif entity == "leads":
        if not partial or "name" in row:
            row["name"] = _clean_text(row.get("name"))
            if not row["name"]:
                raise ValueError("Lead name is required.")
        if "phone" in row:
            row["phone"] = _clean_text(row.get("phone"))
        if "email" in row:
            row["email"] = _clean_text(row.get("email"))
        if "message" in row:
            row["message"] = _clean_text(row.get("message"))
        if not partial or "source" in row:
            row["source"] = _validate_status(
                row.get("source"), LEAD_SOURCES, "Other"
            )
        if not partial or "status" in row:
            row["status"] = _validate_status(
                row.get("status"), LEAD_STATUSES, "New"
            )

    return row


def add_record(entity: str, data: dict[str, Any]) -> dict[str, Any]:
    rows = _read_rows(entity)
    _, id_field, _ = ENTITY_META[entity]
    row = _validate_record(entity, data)
    row[id_field] = _clean_text(row.get(id_field)) or _next_id(entity, rows)
    if any(str(item.get(id_field)) == row[id_field] for item in rows):
        raise ValueError(f"{row[id_field]} already exists.")
    timestamp = _now()
    row.setdefault("created_at", timestamp)
    row["updated_at"] = timestamp
    rows.append(row)
    save_records(entity, rows)
    return row


def get_record(
    entity: str,
    record_id: str,
    *,
    include_archived: bool = False,
) -> dict[str, Any] | None:
    _, id_field, _ = ENTITY_META[entity]
    for row in list_records(entity, include_archived=include_archived):
        if str(row.get(id_field)) == str(record_id):
            return row
    return None


def update_record(
    entity: str,
    record_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    rows = _read_rows(entity)
    _, id_field, _ = ENTITY_META[entity]
    safe_updates = dict(updates)
    safe_updates.pop(id_field, None)
    safe_updates.pop("created_at", None)
    safe_updates = _validate_record(entity, safe_updates, partial=True)
    for index, row in enumerate(rows):
        if str(row.get(id_field)) == str(record_id):
            merged = dict(row)
            merged.update(safe_updates)
            merged = _validate_record(entity, merged)
            merged[id_field] = row[id_field]
            merged["created_at"] = row.get("created_at", _now())
            merged["updated_at"] = _now()
            rows[index] = merged
            save_records(entity, rows)
            return merged
    raise KeyError(f"{entity.rstrip('s').title()} {record_id} was not found.")


def archive_record(entity: str, record_id: str) -> dict[str, Any]:
    return update_record(
        entity,
        record_id,
        {"status": "Archived", "archived": True},
    )


def search_records(
    entity: str,
    query: str = "",
    *,
    fields: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    rows = list_records(entity)
    needle = _clean_text(query).casefold()
    if not needle:
        return rows
    wanted_fields = list(fields or [])
    return [
        row
        for row in rows
        if any(
            needle in str(value).casefold()
            for key, value in row.items()
            if not wanted_fields or key in wanted_fields
        )
    ]


def _patient_name_map() -> dict[str, str]:
    return {
        str(row.get("patient_id")): str(row.get("name", "Unknown patient"))
        for row in list_records("patients")
    }


def records_with_patient_names(entity: str) -> list[dict[str, Any]]:
    names = _patient_name_map()
    rows = []
    for item in list_records(entity):
        row = dict(item)
        if row.get("patient_id"):
            row["patient_name"] = names.get(
                str(row["patient_id"]), "Unknown patient"
            )
        rows.append(row)
    return rows


def patient_profile(patient_id: str) -> dict[str, Any]:
    patient = get_record("patients", patient_id)
    if patient is None:
        raise KeyError(f"Patient {patient_id} was not found.")

    appointments = [
        row
        for row in list_records("appointments")
        if str(row.get("patient_id")) == str(patient_id)
    ]
    packages = [
        row
        for row in list_records("packages")
        if str(row.get("patient_id")) == str(patient_id)
    ]
    payments = [
        row
        for row in list_records("payments")
        if str(row.get("patient_id")) == str(patient_id)
    ]
    progress_notes = [
        row
        for row in list_records("progress_notes")
        if str(row.get("patient_id")) == str(patient_id)
    ]

    completed_dates = sorted(
        [
            row.get("appointment_date", "")
            for row in appointments
            if row.get("status") == "Completed"
            and row.get("appointment_date")
        ]
    )
    scheduled_dates = sorted(
        [
            row.get("appointment_date", "")
            for row in appointments
            if row.get("status") == "Scheduled"
            and row.get("appointment_date")
            and row.get("appointment_date") >= date.today().isoformat()
        ]
    )
    active_packages = [
        row for row in packages if row.get("status") in {"Active", "Paused"}
    ]
    sessions_remaining = sum(
        int(row.get("sessions_remaining", 0) or 0) for row in active_packages
    )
    if not packages:
        sessions_remaining = int(patient.get("sessions_remaining", 0) or 0)

    latest_visit = completed_dates[-1] if completed_dates else _clean_text(
        patient.get("last_visit")
    )
    days_since_visit = None
    if latest_visit:
        days_since_visit = (date.today() - date.fromisoformat(latest_visit)).days

    risk_flags = []
    if str(patient.get("status")) == "Inactive":
        risk_flags.append("Inactive patient")
    if str(patient.get("status")) == "Renewal Due" or (
        active_packages and sessions_remaining <= 2
    ):
        risk_flags.append("Package renewal due")
    if days_since_visit is not None and days_since_visit >= 30:
        risk_flags.append(f"No completed visit for {days_since_visit} days")
    if not bool(patient.get("consent_to_contact", False)):
        risk_flags.append("Contact consent not recorded")
    no_shows = sum(row.get("status") == "No-show" for row in appointments)
    if no_shows >= 2:
        risk_flags.append(f"{no_shows} recorded no-shows")

    return {
        "patient": patient,
        "appointments": sorted(
            appointments,
            key=lambda row: (
                str(row.get("appointment_date", "")),
                str(row.get("appointment_time", "")),
            ),
            reverse=True,
        ),
        "packages": packages,
        "payments": payments,
        "progress_notes": sorted(
            progress_notes,
            key=lambda row: (
                str(row.get("visit_date", "")),
                str(row.get("created_at", "")),
            ),
            reverse=True,
        ),
        "latest_progress": (
            sorted(
                progress_notes,
                key=lambda row: (
                    str(row.get("visit_date", "")),
                    str(row.get("created_at", "")),
                ),
                reverse=True,
            )[0]
            if progress_notes
            else None
        ),
        "last_visit": latest_visit,
        "next_appointment": scheduled_dates[0] if scheduled_dates else "",
        "sessions_remaining": sessions_remaining,
        "payments_total": sum(
            float(row.get("amount", 0) or 0)
            for row in payments
            if row.get("status") == "Paid"
        ),
        "risk_flags": risk_flags,
        "risk_level": (
            "High"
            if len(risk_flags) >= 3
            else "Medium"
            if risk_flags
            else "Low"
        ),
    }


def patient_risk_summary() -> list[dict[str, Any]]:
    results = []
    for patient in list_records("patients"):
        profile = patient_profile(str(patient.get("patient_id")))
        results.append(
            {
                "patient_id": patient.get("patient_id"),
                "name": patient.get("name"),
                "status": patient.get("status"),
                "risk_level": profile["risk_level"],
                "risk_flags": "; ".join(profile["risk_flags"]) or "No current flags",
                "sessions_remaining": profile["sessions_remaining"],
                "last_visit": profile["last_visit"],
                "next_appointment": profile["next_appointment"],
                "consent_to_contact": bool(
                    patient.get("consent_to_contact", False)
                ),
            }
        )
    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(
        results,
        key=lambda row: (order.get(str(row["risk_level"]), 9), str(row["name"])),
    )


def clinic_metrics() -> dict[str, Any]:
    patients = list_records("patients")
    appointments = list_records("appointments")
    packages = list_records("packages")
    payments = list_records("payments")
    therapists = list_records("therapists")
    risk_rows = patient_risk_summary()
    today = date.today().isoformat()
    return {
        "patients": len(patients),
        "active_patients": sum(
            str(row.get("status", "")).lower() == "active" for row in patients
        ),
        "appointments": len(appointments),
        "upcoming_appointments": sum(
            row.get("status") == "Scheduled"
            and str(row.get("appointment_date", "")) >= today
            for row in appointments
        ),
        "packages": len(packages),
        "renewals_due": sum(
            row.get("status") == "Active"
            and int(row.get("sessions_remaining", 0) or 0) <= 2
            for row in packages
        ),
        "payments_total": sum(
            float(row.get("amount", 0) or 0)
            for row in payments
            if row.get("status", "Paid") == "Paid"
        ),
        "pending_payments": sum(
            row.get("status") == "Pending" for row in payments
        ),
        "therapists": len(therapists),
        "at_risk_patients": sum(
            row.get("risk_level") in {"High", "Medium"} for row in risk_rows
        ),
    }
