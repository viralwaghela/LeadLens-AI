"""Allowlisted, read-only data tools for LeadLens specialist agents."""
from __future__ import annotations

import copy
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

# Phase 7 RBAC: tools whose ENTIRE output is finance-sensitive vs. tools
# that merely have one finance-sensitive field mixed into otherwise-safe
# data. business_snapshot was found (Phase 7 audit-of-self) to leak the
# same financial_snapshot data revenue_summary does, under a different
# tool name reachable by non-finance specialists (marketing, hr,
# operations) — a real "Jarvis as a backdoor to finance data" gap this
# closes at the tool/data boundary itself, per docs/V2_PHASE7_AUTH_CUTOVER.md,
# not merely by instructing the model in a prompt.
_FINANCE_ONLY_TOOLS = {"revenue_summary"}
_FINANCE_SENSITIVE_FIELDS = {"business_snapshot": ("financial_snapshot",)}
_FINANCE_PERMISSION = "jarvis.finance"


def run_read_only_tool(
    name: str,
    context: dict[str, Any],
    *,
    permissions: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Run an explicitly allowlisted tool without mutating business state.

    `permissions`, when given (Phase 7 — RBAC is active), gates finance-
    sensitive output at this data boundary: a caller without
    "jarvis.finance" gets an explicit access-denied stub for a
    finance-only tool, or the same tool output with finance-sensitive
    fields stripped for a tool that mixes sensitive and non-sensitive
    data. `permissions=None` (the default) reproduces exactly the
    pre-Phase-7 behavior — every existing caller (RBAC not active) is
    unaffected."""
    try:
        tool = READ_ONLY_TOOLS[name]
    except KeyError as error:
        raise ValueError(f"Unknown or non-read-only Jarvis tool: {name}") from error
    result = tool(context)
    if permissions is None or _FINANCE_PERMISSION in permissions:
        return result

    if name in _FINANCE_ONLY_TOOLS:
        return {"source": result.get("source", ""), "data": {}, "access_denied": True, "reason": "jarvis.finance permission required"}

    sensitive_fields = _FINANCE_SENSITIVE_FIELDS.get(name)
    if sensitive_fields:
        redacted = copy.deepcopy(result)
        data = redacted.get("data", {})
        for field in sensitive_fields:
            data.pop(field, None)
        redacted["access_denied_fields"] = list(sensitive_fields)
        return redacted

    return result
