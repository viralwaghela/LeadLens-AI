
"""Business context, safeguards and live OpenAI reasoning for Jarvis."""
from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from core.memory import (
    add_approval,
    add_daily_log,
    add_decision,
    add_memory_entry,
    add_task,
    load_company,
    load_memory,
)
from services.jarvis_context import build_jarvis_context


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _text_from_entry(entry: dict[str, Any]) -> str:
    data = entry.get("data", {}) if isinstance(entry, dict) else {}
    return " ".join(str(v) for v in data.values() if v is not None)


def build_business_context() -> dict[str, Any]:
    return build_jarvis_context()


def priorities(context: dict[str, Any]) -> list[dict[str, Any]]:
    s = context["signals"]
    items: list[dict[str, Any]] = []

    if s["renewals_due"] or s["patient_inactivity"]:
        items.append({
            "title": "Recover and renew patients",
            "owner": "Customer Success",
            "why": "This is the fastest path to revenue using existing patient relationships.",
            "impact": "High",
            "action": "Prepare inactive-patient and package-renewal outreach.",
        })
    if s["corporate_interest"]:
        items.append({
            "title": "Convert corporate enquiries",
            "owner": "Sales",
            "why": "Warm corporate demand can produce larger contracts than individual sessions.",
            "impact": "High",
            "action": "Prepare a corporate wellness proposal and assign follow-up dates.",
        })
    if s["capacity_risk"]:
        items.append({
            "title": "Protect therapist capacity",
            "owner": "Operations",
            "why": "Overload risks service quality, cancellations and staff burnout.",
            "impact": "High",
            "action": "Redistribute slots and evaluate part-time therapist coverage.",
        })
    if s["bookings_down"]:
        items.append({
            "title": "Diagnose booking decline",
            "owner": "Analytics",
            "why": "The clinic should separate demand, conversion and capacity causes before spending more.",
            "impact": "High",
            "action": "Compare enquiries, bookings, cancellations and channel conversion.",
        })
    if s["marketing_cost_up"]:
        items.append({
            "title": "Control acquisition cost",
            "owner": "Marketing",
            "why": "Higher spend without measured conversion can weaken operating margin.",
            "impact": "Medium",
            "action": "Pause weak campaigns and track cost per booked patient.",
        })
    baseline = [
        {
            "title": "Grow recurring clinic revenue",
            "owner": "Growth",
            "why": "Packages and memberships improve predictability.",
            "impact": "High",
            "action": "Review renewals, referrals and corporate wellness opportunities.",
        },
        {
            "title": "Improve management visibility",
            "owner": "Analytics",
            "why": "Weekly operating metrics reduce reactive decision-making.",
            "impact": "Medium",
            "action": "Track bookings, utilisation, revenue and churn weekly.",
        },
        {
            "title": "Close open management actions",
            "owner": "Chief of Staff",
            "why": "Unowned or stale actions reduce execution reliability.",
            "impact": "Medium",
            "action": "Assign one owner, outcome and review date to each open action.",
        },
    ]
    existing_titles = {item["title"] for item in items}
    for item in baseline:
        if item["title"] not in existing_titles:
            items.append(item)
    return items[:5]


def _safe_recent_records(
    records: list[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Whitelist business fields so patient/contact details never enter prompts."""
    allowed = {
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
    }
    output: list[dict[str, Any]] = []
    for entry in records[-limit:]:
        data = entry.get("data", {}) if isinstance(entry, dict) else {}
        clean = {
            key: value
            for key, value in data.items()
            if key in allowed and value not in (None, "", [], {})
        }
        if clean:
            output.append(clean)
    return output


def build_jarvis_ai_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return the privacy-safe, source-aware representation for the model."""
    ai_context = dict(context.get("model_context", {}))
    ai_context["deterministic_priority_candidates"] = priorities(context)
    return ai_context


def _deterministic_chief_of_staff_answer(
    context: dict[str, Any],
    fallback_notice: bool = False,
) -> str:
    """Safe local response used only when live OpenAI is unavailable."""
    c = context
    p = priorities(c)[:3]
    target = _num(c["company"].get("target_monthly_revenue"), 700000)
    gap = max(0, target - c["revenue"])

    lines: list[str] = []
    if fallback_notice:
        lines += [
            "> **Live AI is temporarily unavailable.** "
            "This is the local evidence-based fallback; no external action was taken.",
            "",
        ]
    lines += [
        "## Chief of Staff assessment",
        "",
        f"**Current position:** Revenue ₹{c['revenue']:,.0f}, estimated profit ₹{c['profit']:,.0f}, "
        f"operating margin {c['margin']:.1f}%, {c['open_tasks']} open actions and "
        f"{c['pending_approvals']} pending approvals.",
        "",
        "**Recommended priority order**",
    ]
    for idx, item in enumerate(p, 1):
        lines.append(
            f"{idx}. **{item['title']}** — {item['why']} "
            f"**Next action:** {item['action']} **Owner:** {item['owner']}."
        )
    if gap:
        lines += [
            "",
            f"**Revenue gap to the working target:** ₹{gap:,.0f} per month.",
            "The safest path is to recover existing revenue first, convert warm corporate demand second, "
            "and add fixed capacity only after demand is verified.",
        ]
    lines += [
        "",
        "**Management rule:** Approve one owner, one measurable outcome and one review date for every priority.",
    ]
    return "\n".join(lines)


def dynamic_chief_of_staff_answer(
    query: str,
    conversation_history: list[dict[str, Any]] | None = None,
) -> str:
    """Coordinate read-only specialists and return Jarvis's synthesis."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return "Please ask a business question so I can review the evidence."
    from services.authorization_guard import current_permissions
    from services.specialist_orchestration import coordinate_specialists

    return str(
        coordinate_specialists(
            clean_query,
            conversation_history=conversation_history,
            permissions=current_permissions(),
        )["message"]
    )


def simulate_scenario(
    scenario: str,
    current_revenue: float,
    monthly_cost: float,
    capacity_increase_pct: float,
    conversion_revenue: float,
) -> dict[str, Any]:
    scenario = scenario.strip()
    capacity_gain = current_revenue * max(capacity_increase_pct, 0) / 100
    projected_gain = max(conversion_revenue, 0) + capacity_gain
    projected_revenue = current_revenue + projected_gain
    monthly_net_gain = projected_gain - max(monthly_cost, 0)
    payback = (monthly_cost / monthly_net_gain) if monthly_net_gain > 0 and monthly_cost > 0 else 0

    return {
        "scenario": scenario,
        "projected_revenue": projected_revenue,
        "monthly_gain": projected_gain,
        "monthly_net_gain": monthly_net_gain,
        "payback_months": payback,
        "recommendation": (
            "Proceed as a controlled 30-day pilot with clear conversion and utilisation targets."
            if monthly_net_gain > 0
            else "Do not commit to fixed cost yet. Validate demand with a low-cost pilot first."
        ),
    }


def revenue_plan(target: float) -> dict[str, Any]:
    c = build_business_context()
    gap = max(0, target - c["revenue"])
    allocations = [
        ("Patient renewals and recovery", 0.25),
        ("Corporate wellness contracts", 0.35),
        ("Pilates or recurring memberships", 0.20),
        ("Improved capacity and slot utilisation", 0.15),
        ("Referrals and reviews", 0.05),
    ]
    rows = [
        {"channel": name, "monthly_target": round(gap * share), "share": share}
        for name, share in allocations
    ]
    return {"current": c["revenue"], "target": target, "gap": gap, "channels": rows}


def agent_council() -> list[dict[str, str]]:
    c = build_business_context()
    s = c["signals"]
    return [
        {
            "agent": "Marketing Agent",
            "finding": "Marketing spend requires tighter booked-patient attribution."
            if s["marketing_cost_up"] else "Focus marketing on measurable bookings and renewals.",
            "recommendation": "Track cost per enquiry, booking and retained patient.",
        },
        {
            "agent": "Operations Agent",
            "finding": "Therapist capacity is under pressure." if s["capacity_risk"] else "No critical capacity event is recorded.",
            "recommendation": "Balance morning/evening slots and protect therapist admin time.",
        },
        {
            "agent": "Finance Agent",
            "finding": f"Operating margin is {c['margin']:.1f}%.",
            "recommendation": "Use pilots before taking on fixed costs; measure monthly net gain.",
        },
        {
            "agent": "Customer Success Agent",
            "finding": "Renewal or inactivity signals need attention." if (s["renewals_due"] or s["patient_inactivity"]) else "Retention should remain a weekly metric.",
            "recommendation": "Run an approval-first patient recovery workflow.",
        },
        {
            "agent": "Sales Agent",
            "finding": "Corporate interest is available for conversion." if s["corporate_interest"] else "Build a repeatable corporate wellness pipeline.",
            "recommendation": "Use a standard proposal, next action and follow-up date.",
        },
    ]


def meeting_brief() -> dict[str, Any]:
    c = build_business_context()
    return {
        "generated_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "headline": f"{c['company'].get('business_name', 'The clinic')} has {c['open_tasks']} open actions "
                    f"and an operating margin of {c['margin']:.1f}%.",
        "priorities": priorities(c),
        "questions": [
            "Which priority will the owner personally approve today?",
            "Which metric will prove the action worked?",
            "What should be stopped or postponed this week?",
        ],
    }


def record_learning(observation: str, outcome: str) -> None:
    add_memory_entry("reports", {
        "type": "Jarvis Learning",
        "title": observation,
        "outcome": outcome,
        "status": "Learned",
    })
    add_daily_log(f"Jarvis learned: {observation}. Outcome: {outcome}")


def prepare_execution(workflow: str, recipient: str, message: str) -> dict[str, Any]:
    task = add_task(f"Execute: {workflow}", "Operations", "High")
    approval = add_approval(f"Approve execution: {workflow}", "Operations", "High")
    draft = add_memory_entry("reports", {
        "type": "Execution Draft",
        "title": workflow,
        "recipient": recipient,
        "message": message,
        "status": "Awaiting approval",
    })
    return {"task": task, "approval": approval, "draft": draft}
