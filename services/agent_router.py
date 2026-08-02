"""Simple query router for the LeadLens Chief of Staff workspace.

This module only decides which specialist agents are relevant. It does not
execute department workflows yet, so existing V1 functionality remains safe.
"""

from __future__ import annotations

from collections.abc import Iterable


_AGENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sales": (
        "sales",
        "revenue",
        "lead",
        "leads",
        "pipeline",
        "deal",
        "deals",
        "conversion",
        "customer acquisition",
    ),
    "marketing": (
        "marketing",
        "campaign",
        "campaigns",
        "advertising",
        "ads",
        "content",
        "social media",
        "brand",
        "engagement",
    ),
    "finance": (
        "finance",
        "financial",
        "budget",
        "expense",
        "expenses",
        "profit",
        "cash flow",
        "cost",
        "costs",
    ),
    "operations": (
        "operations",
        "operation",
        "workflow",
        "process",
        "productivity",
        "bottleneck",
        "task",
        "tasks",
        "delivery",
        "booking",
        "bookings",
        "appointment",
        "appointments",
        "capacity",
        "therapist",
        "therapists",
    ),
    "hr": (
        "hr",
        "human resources",
        "employee",
        "employees",
        "hiring",
        "recruitment",
        "team",
        "attendance",
        "hire",
        "headcount",
        "staff",
    ),
    "customer_success": (
        "patient",
        "patients",
        "retention",
        "renewal",
        "renewals",
        "inactive",
        "follow-up",
        "follow up",
        "churn",
    ),
    "analytics": (
        "analytics",
        "trend",
        "trends",
        "complete business review",
        "business overview",
        "full review",
    ),
}


def _contains_any(query: str, keywords: Iterable[str]) -> bool:
    return any(keyword in query for keyword in keywords)


def determine_agents(query: str) -> list[str]:
    """Return relevant agent names for a user query.

    Multiple agents may be returned. When no department-specific keyword is
    found, the executive agent is selected as a safe default.
    """

    normalized_query = str(query or "").strip().lower()

    if not normalized_query:
        return ["analytics"]

    complete_review_phrases = (
        "complete business review",
        "complete business overview",
        "full business review",
        "review the whole business",
        "everything in the business",
    )
    if _contains_any(normalized_query, complete_review_phrases):
        return [
            "sales",
            "marketing",
            "finance",
            "operations",
            "hr",
            "customer_success",
            "analytics",
        ]

    booking_decline_phrases = (
        "bookings falling",
        "bookings down",
        "booking decline",
        "appointments falling",
        "appointments down",
    )
    if _contains_any(normalized_query, booking_decline_phrases):
        return ["marketing", "operations", "customer_success"]

    if (
        _contains_any(normalized_query, ("therapist", "staff", "employee"))
        and _contains_any(
            normalized_query,
            ("afford", "hire", "hiring", "add another", "headcount"),
        )
    ):
        return ["finance", "hr", "operations"]

    selected_agents = [
        agent_name
        for agent_name, keywords in _AGENT_KEYWORDS.items()
        if _contains_any(normalized_query, keywords)
    ]

    return selected_agents or ["analytics"]
