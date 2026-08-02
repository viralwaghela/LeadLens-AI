from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from services.business_jarvis_engine import build_business_context
from services.learning_memory_v22 import recommendation_context

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "collaboration" / "council_sessions.json"
STORE.parent.mkdir(parents=True, exist_ok=True)


def _save_session(session: dict[str, Any]) -> None:
    try:
        rows = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else []
    except (OSError, json.JSONDecodeError):
        rows = []
    rows.append(session)
    STORE.write_text(json.dumps(rows[-500:], indent=2, ensure_ascii=False), encoding="utf-8")


def _marketing(context: dict[str, Any], question: str) -> dict[str, str]:
    signal = context["signals"].get("marketing_cost_up")
    return {"agent": "Marketing Agent", "finding": "Acquisition efficiency needs attention." if signal else "Marketing should prioritise measurable bookings and retention.", "recommendation": "Use the highest-converting measured channel and stop campaigns without booked-patient attribution.", "risk": "Spending may rise without verified revenue."}


def _operations(context: dict[str, Any], question: str) -> dict[str, str]:
    signal = context["signals"].get("capacity_risk")
    return {"agent": "Operations Agent", "finding": "Capacity pressure is present." if signal else "No critical capacity pressure is recorded.", "recommendation": "Confirm available slots before increasing demand and protect therapist workload.", "risk": "Growth without capacity can increase cancellations and poor service."}


def _finance(context: dict[str, Any], question: str) -> dict[str, str]:
    return {"agent": "Finance Agent", "finding": f"Estimated operating margin is {context['margin']:.1f}%.", "recommendation": "Prioritise actions with measured net revenue and avoid fixed costs until demand is proven.", "risk": "Revenue growth can hide weak cash contribution."}


def _customer_success(context: dict[str, Any], question: str) -> dict[str, str]:
    retention_signal = context["signals"].get("renewals_due") or context["signals"].get("patient_inactivity")
    return {"agent": "Customer Success Agent", "finding": "Renewal or inactivity signals require action." if retention_signal else "Retention remains the lowest-cost growth route.", "recommendation": "Contact consented inactive and renewal-due patients using approval-first workflows.", "risk": "Delayed follow-up allows preventable churn."}


def _sales(context: dict[str, Any], question: str) -> dict[str, str]:
    signal = context["signals"].get("corporate_interest")
    return {"agent": "Sales Agent", "finding": "Warm corporate interest is recorded." if signal else "Corporate outreach needs a repeatable pipeline.", "recommendation": "Use a standard offer, owner, next action and follow-up date for every lead.", "risk": "Unowned leads may become stale."}


def run_agent_council(question: str) -> dict[str, Any]:
    question = str(question or "").strip()
    context = build_business_context()
    learning = recommendation_context()
    responses = [
        _marketing(context, question),
        _operations(context, question),
        _finance(context, question),
        _customer_success(context, question),
        _sales(context, question),
    ]
    top_pattern = learning["best_patterns"][0] if learning["best_patterns"] else None
    priorities = []
    if context["signals"].get("renewals_due") or context["signals"].get("patient_inactivity"):
        priorities.append("Run a consented patient recovery and renewal workflow.")
    if context["signals"].get("corporate_interest"):
        priorities.append("Assign and follow up all warm corporate opportunities.")
    if context["signals"].get("capacity_risk"):
        priorities.append("Resolve capacity constraints before adding demand.")
    if top_pattern:
        priorities.append(f"Reuse the strongest measured pattern: {top_pattern['action_type']} ({top_pattern['success_rate_percent']}% success rate over {top_pattern['measured_runs']} runs, {top_pattern['confidence']} confidence).")
    if not priorities:
        priorities = ["Prioritise retention, measurable booking conversion and weekly capacity review."]

    synthesis = {
        "decision": priorities[0],
        "supporting_actions": priorities[1:4],
        "management_rule": "One owner, one approval, one measurable outcome and one review date.",
        "learning_used": bool(top_pattern),
    }
    session = {"id": f"COUNCIL-{uuid4().hex[:10].upper()}", "question": question, "created_at": datetime.now().isoformat(timespec="seconds"), "agents": responses, "synthesis": synthesis}
    _save_session(session)
    return session


def council_sessions() -> list[dict[str, Any]]:
    if not STORE.exists():
        return []
    try:
        return list(reversed(json.loads(STORE.read_text(encoding="utf-8"))))
    except (OSError, json.JSONDecodeError):
        return []
