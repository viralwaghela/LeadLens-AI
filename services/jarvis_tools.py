"""Allowlisted, read-only data tools for LeadLens specialist agents."""
from __future__ import annotations

from typing import Any, Callable


def _facts(context: dict[str, Any]) -> dict[str, Any]:
    return context.get("model_context", {}).get("confirmed_facts", {})


def business_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    facts = _facts(context)
    return {
        "source": "business.memory",
        "data": {
            "business": facts.get("business", {}),
            "financial_snapshot": facts.get("financial_snapshot", {}),
            "work_snapshot": facts.get("work_snapshot", {}),
        },
    }


def revenue_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "business.memory",
        "data": _facts(context).get("financial_snapshot", {}),
    }


def patient_risks(context: dict[str, Any]) -> dict[str, Any]:
    clinic = _facts(context).get("clinic_aggregates", {})
    return {
        "sources": ["clinic.patients", "clinic.packages"],
        "data": {
            "patients": clinic.get("patients", {}),
            "packages": clinic.get("packages", {}),
            "inactivity_signal": context.get("signals", {}).get(
                "patient_inactivity", False
            ),
            "renewal_signal": context.get("signals", {}).get(
                "renewals_due", False
            ),
        },
    }


def therapist_capacity(context: dict[str, Any]) -> dict[str, Any]:
    clinic = _facts(context).get("clinic_aggregates", {})
    return {
        "sources": ["clinic.therapists", "clinic.appointments"],
        "data": {
            "therapists": clinic.get("therapists", {}),
            "appointments": clinic.get("appointments", {}),
            "capacity_risk_inference": context.get("signals", {}).get(
                "capacity_risk", False
            ),
        },
    }


def lead_pipeline(context: dict[str, Any]) -> dict[str, Any]:
    clinic = _facts(context).get("clinic_aggregates", {})
    return {
        "sources": ["clinic.leads", "clinic.corporate_clients"],
        "data": {
            "leads": clinic.get("leads", {}),
            "corporate_clients": clinic.get("corporate_clients", {}),
            "corporate_interest_inference": context.get("signals", {}).get(
                "corporate_interest", False
            ),
        },
    }


def management_work(context: dict[str, Any]) -> dict[str, Any]:
    facts = _facts(context)
    return {
        "source": "business.memory",
        "data": {
            "work_snapshot": facts.get("work_snapshot", {}),
            "recent_decisions": facts.get("recent_decisions", []),
            "recent_reports": facts.get("recent_reports", []),
        },
    }


def learning_signals(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "learning.memory",
        "data": _facts(context).get("learning_memory", {}),
    }


READ_ONLY_TOOLS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "business_snapshot": business_snapshot,
    "revenue_summary": revenue_summary,
    "patient_risks": patient_risks,
    "therapist_capacity": therapist_capacity,
    "lead_pipeline": lead_pipeline,
    "management_work": management_work,
    "learning_signals": learning_signals,
}


def run_read_only_tool(
    name: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Run an explicitly allowlisted tool without mutating business state."""
    try:
        tool = READ_ONLY_TOOLS[name]
    except KeyError as error:
        raise ValueError(f"Unknown or non-read-only Jarvis tool: {name}") from error
    return tool(context)
