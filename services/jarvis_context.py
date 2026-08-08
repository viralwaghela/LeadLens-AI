"""Privacy-safe, source-aware context for Jarvis.

This module is the single read path for business facts supplied to the LLM.
It intentionally aggregates clinic records so patient and staff identities,
contact details, and free-form clinical notes are never placed in prompts.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Any

from core.memory import load_memory
from services.clinic_data_service import list_records as _list_clinic_records
from services.jarvis_memory import relevant_memory


ROOT = Path(__file__).resolve().parents[1]
LEARNING_FILE = ROOT / "data" / "learning" / "learning_memory.json"
MEMORY_FILE = ROOT / "database" / "company.json"

SENSITIVE_KEYS = {
    "name",
    "owner_name",
    "founder_name",
    "patient_name",
    "therapist_name",
    "phone",
    "mobile",
    "email",
    "business_email",
    "recipient",
    "address",
    "notes",
    "message",
    "clinical_notes",
    "diagnosis",
}

RECENT_ALLOWED_KEYS = {
    "title",
    "summary",
    "text",
    "type",
    "status",
    "department",
    "priority",
    "impact",
    "outcome",
    "recommendation",
    "reason",
    "risk_level",
    "activity_type",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path, default: Any) -> tuple[Any, str | None]:
    try:
        if not path.exists():
            return default, "file not found"
        return json.loads(path.read_text(encoding="utf-8") or "null"), None
    except (OSError, json.JSONDecodeError) as error:
        return default, f"unreadable JSON: {error}"


def _modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(
            timespec="seconds"
        )
    except OSError:
        return None


def _status_count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get(key, "Unknown") or "Unknown").strip().title()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _recent_records(
    records: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    clean_records: list[dict[str, Any]] = []
    for entry in records[-limit:]:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data", {})
        if not isinstance(data, dict):
            continue
        clean = {
            key: value
            for key, value in data.items()
            if key in RECENT_ALLOWED_KEYS and value not in (None, "", [], {})
        }
        if clean:
            clean_records.append({
                "recorded_at": entry.get("created_at"),
                **clean,
            })
    return clean_records


def _clinic_aggregates(
    collections: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    patients = collections["patients"]
    appointments = collections["appointments"]
    packages = collections["packages"]
    payments = collections["payments"]
    therapists = collections["therapists"]
    leads = collections["leads"]
    corporate_clients = collections["corporate_clients"]

    inactive_patients = sum(
        str(row.get("status", "")).strip().lower() == "inactive"
        for row in patients
    )
    low_session_patients = sum(
        0 < _number(row.get("sessions_remaining")) <= 2
        for row in patients
    )
    active_therapists = [
        row for row in therapists
        if str(row.get("status", "active")).strip().lower() == "active"
    ]
    today = date.today()
    renewal_window = today + timedelta(days=14)
    active_patient_ids = {
        str(row.get("patient_id"))
        for row in patients
        if str(row.get("status", "")).strip().lower() != "archived"
    }
    renewal_patient_ids: set[str] = set()
    for row in packages:
        if str(row.get("status", "")).strip().lower() != "active":
            continue
        sessions_remaining = int(_number(row.get("sessions_remaining")))
        expiry_text = str(row.get("expiry_date", "") or "")
        expires_soon = False
        if expiry_text:
            try:
                expiry = date.fromisoformat(expiry_text)
                expires_soon = today <= expiry <= renewal_window
            except ValueError:
                expires_soon = False
        if sessions_remaining <= 2 or expires_soon:
            patient_id = str(row.get("patient_id", ""))
            if patient_id in active_patient_ids:
                renewal_patient_ids.add(patient_id)

    upcoming_appointments = 0
    for row in appointments:
        if str(row.get("status", "")).strip().lower() != "scheduled":
            continue
        try:
            appointment_date = date.fromisoformat(
                str(row.get("appointment_date", ""))
            )
        except ValueError:
            continue
        if appointment_date >= today:
            upcoming_appointments += 1

    pending_payments = [
        row for row in payments
        if str(row.get("status", "")).strip().lower() == "pending"
    ]
    consented_patients = sum(
        bool(row.get("consent_to_contact", False)) for row in patients
    )
    at_risk_patient_ids = set(renewal_patient_ids)
    at_risk_patient_ids.update(
        str(row.get("patient_id"))
        for row in patients
        if str(row.get("status", "")).strip().lower()
        in {"inactive", "renewal due"}
    )

    return {
        "patients": {
            "total": len(patients),
            "status_counts": _status_count(patients, "status"),
            "inactive_count": inactive_patients,
            "patients_with_1_to_2_sessions_remaining": low_session_patients,
            "consent_to_contact_recorded": consented_patients,
            "at_risk_count": len(at_risk_patient_ids),
        },
        "appointments": {
            "total": len(appointments),
            "status_counts": _status_count(appointments, "status"),
            "upcoming_scheduled": upcoming_appointments,
        },
        "packages": {
            "total": len(packages),
            "status_counts": _status_count(packages, "status"),
            "renewals_due": len(renewal_patient_ids),
        },
        "payments": {
            "records": len(payments),
            "recorded_total": round(
                sum(_number(row.get("amount")) for row in payments),
                2,
            ),
            "pending_records": len(pending_payments),
            "pending_total": round(
                sum(_number(row.get("amount")) for row in pending_payments),
                2,
            ),
        },
        "therapists": {
            "total": len(therapists),
            "active": len(active_therapists),
            "combined_weekly_capacity": round(
                sum(_number(row.get("weekly_capacity"))
                    for row in active_therapists),
                1,
            ),
        },
        "leads": {
            "total": len(leads),
            "status_counts": _status_count(leads, "status"),
        },
        "corporate_clients": {
            "total": len(corporate_clients),
            "status_counts": _status_count(corporate_clients, "status"),
        },
    }


def build_jarvis_context(query: str = "") -> dict[str, Any]:
    """Return full internal context plus a safe LLM-ready representation."""
    memory = load_memory()
    company = memory.get("company", {})

    collections: dict[str, list[dict[str, Any]]] = {}
    source_status: list[dict[str, Any]] = []
    unavailable: list[str] = []

    for entity in (
        "patients",
        "appointments",
        "packages",
        "payments",
        "therapists",
        "leads",
        "corporate_clients",
    ):
        # Used to read data/pilot/{entity}.json, a leftover from before
        # clinic_data_service.py was migrated onto core.memory (Postgres/
        # SQLite) — that directory doesn't exist in this deployment, so
        # every clinic collection here was silently empty, always. Read
        # from the same store the CRM actually writes to instead.
        try:
            rows = _list_clinic_records(entity)
            error = None
        except Exception as exc:  # pragma: no cover - defensive only
            rows = []
            error = str(exc)
        collections[entity] = [
            row for row in rows if isinstance(row, dict)
        ]
        source_status.append({
            "source": f"clinic.{entity}",
            "loaded": error is None,
            "records": len(collections[entity]),
            "updated_at": None,
        })
        if error:
            unavailable.append(f"clinic.{entity}: {error}")

    learning, learning_error = _read_json(LEARNING_FILE, {})
    if not isinstance(learning, dict):
        learning = {}
        learning_error = learning_error or "expected a JSON object"
    source_status.append({
        "source": "learning.memory",
        "loaded": learning_error is None,
        "records": sum(
            len(learning.get(key, []))
            for key in ("outcomes", "preferences", "patterns")
            if isinstance(learning.get(key, []), list)
        ),
        "updated_at": learning.get("updated_at")
        or _modified_at(LEARNING_FILE),
    })
    if learning_error:
        unavailable.append(f"learning.memory: {learning_error}")

    source_status.insert(0, {
        "source": "business.memory",
        "loaded": True,
        "records": sum(
            len(memory.get(key, []))
            for key in (
                "daily_logs",
                "decisions",
                "tasks",
                "completed_tasks",
                "approvals",
                "reports",
            )
        ),
        "updated_at": _modified_at(MEMORY_FILE),
    })

    # Prefer real Payments records over the manually-typed company profile
    # field, which otherwise silently drifts from what's actually recorded
    # in the CRM (see clinic_data_service.py). Only fall back to the manual
    # figure when the clinic has never recorded a payment at all — a brand
    # new setup, or a non-clinic business that isn't using Payments.
    clinic_payments = collections["payments"]
    if clinic_payments:
        today = date.today()
        clinic_revenue = 0.0
        for row in clinic_payments:
            if str(row.get("status", "")).strip().lower() != "paid":
                continue
            try:
                payment_date = date.fromisoformat(str(row.get("payment_date", "") or ""))
            except ValueError:
                continue
            if payment_date.year == today.year and payment_date.month == today.month:
                clinic_revenue += _number(row.get("amount"))
        revenue = round(clinic_revenue, 2)
        revenue_source = "clinic.payments"
    else:
        revenue = _number(company.get("monthly_revenue"))
        revenue_source = "business.memory"
    expenses = _number(company.get("monthly_expenses"))
    profit = revenue - expenses
    margin = (profit / revenue * 100) if revenue else 0.0

    tasks = memory.get("tasks", [])
    pending_approvals = [
        item for item in memory.get("approvals", [])
        if str(item.get("data", {}).get("status", "Pending")).lower()
        in {"pending", "open"}
    ]
    recent_logs = _recent_records(memory.get("daily_logs", []), 6)
    recent_decisions = _recent_records(memory.get("decisions", []), 6)
    recent_reports = _recent_records(memory.get("reports", []), 10)
    clinic = _clinic_aggregates(collections)

    corpus = " ".join(
        str(value)
        for record in recent_logs + recent_decisions + recent_reports
        for value in record.values()
        if value is not None
    ).lower()
    signals = {
        "bookings_down": any(
            term in corpus
            for term in ("bookings dropped", "bookings down", "appointment drop")
        ),
        "capacity_risk": any(
            term in corpus
            for term in ("overloaded", "evening slots full", "utilisation")
        ),
        "renewals_due": (
            clinic["packages"]["renewals_due"] > 0
            or any(term in corpus for term in ("renewal", "expiring package"))
        ),
        "corporate_interest": (
            clinic["corporate_clients"]["total"] > 0
            or any(term in corpus for term in ("corporate enquiries", "corporate leads"))
        ),
        "marketing_cost_up": any(
            term in corpus
            for term in ("marketing costs increased", "ads expensive", "cac increased")
        ),
        "patient_inactivity": clinic["patients"]["inactive_count"] > 0,
    }

    safe_company = {
        key: value
        for key, value in company.items()
        if key not in SENSITIVE_KEYS | {"permissions", "platforms"}
    }
    safe_company["recorded_operational_channels"] = company.get(
        "platforms", []
    )
    safe_company["channel_connection_status"] = (
        "Not verified by this context. A recorded channel is not proof of "
        "a live integration."
    )
    safe_company["recorded_owner_automation_preferences"] = company.get(
        "permissions", []
    )
    safe_company["runtime_authorization_status"] = (
        "Read-only consultation only. Recorded preferences do not authorize "
        "external actions."
    )
    safe_learning = relevant_memory(query, limit=8)

    model_context = {
        "context_generated_at": datetime.now().isoformat(timespec="seconds"),
        "grounding_rules": {
            "confirmed_facts": (
                "Values below were read from the listed LeadLens sources."
            ),
            "inferences": (
                "Derived signals must be described as inferences, not confirmed events."
            ),
            "missing_data": (
                "If a required fact is absent, say it is unavailable and do not guess."
            ),
        },
        "confirmed_facts": {
            "business": {
                "_source": "business.memory",
                **safe_company,
            },
            "financial_snapshot": {
                "_source": revenue_source,
                "monthly_revenue": revenue,
                "monthly_revenue_note": (
                    "Sum of this calendar month's Paid clinic payments."
                    if revenue_source == "clinic.payments"
                    else "No clinic payment records exist yet; this is the "
                    "manually-entered company profile figure, which may be stale."
                ),
                "monthly_expenses": expenses,
                "estimated_profit": profit,
                "operating_margin_percent": round(margin, 1),
            },
            "work_snapshot": {
                "_source": "business.memory",
                "open_tasks": len(tasks),
                "pending_approvals": len(pending_approvals),
            },
            "clinic_aggregates": {
                "_sources": [
                    "clinic.patients",
                    "clinic.appointments",
                    "clinic.packages",
                    "clinic.payments",
                    "clinic.therapists",
                    "clinic.leads",
                    "clinic.corporate_clients",
                ],
                **clinic,
            },
            "recent_business_logs": recent_logs,
            "recent_decisions": recent_decisions,
            "recent_reports": recent_reports,
            "learning_memory": {
                "_source": "learning.memory",
                **safe_learning,
            },
        },
        "derived_signals": signals,
        "source_register": source_status,
        "unavailable_sources": unavailable,
    }

    return {
        "memory": memory,
        "company": company,
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "margin": margin,
        "open_tasks": len(tasks),
        "pending_approvals": len(pending_approvals),
        "signals": signals,
        "recent_logs": memory.get("daily_logs", [])[-6:],
        "recent_decisions": memory.get("decisions", [])[-6:],
        "clinic_aggregates": clinic,
        "source_status": source_status,
        "unavailable_sources": unavailable,
        "model_context": model_context,
        "privacy": {
            "mode": "aggregate_only",
            "excluded_fields": sorted(SENSITIVE_KEYS),
        },
    }


def context_audit(context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a compact diagnostic suitable for the UI and tests."""
    current = context or build_jarvis_context()
    sources = current.get("source_status", [])
    loaded = [source for source in sources if source.get("loaded")]
    non_empty = [
        source for source in loaded
        if int(source.get("records", 0) or 0) > 0
    ]
    return {
        "status": (
            "Ready"
            if loaded and not current.get("unavailable_sources")
            else "Needs attention"
        ),
        "sources_loaded": len(loaded),
        "sources_with_records": len(non_empty),
        "sources_total": len(sources),
        "unavailable_sources": current.get("unavailable_sources", []),
        "privacy_mode": current.get("privacy", {}).get("mode"),
        "source_status": sources,
    }
