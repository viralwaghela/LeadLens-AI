"""Regression tests for specialist routing and read-only orchestration."""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from services.agent_router import determine_agents
from services.jarvis_context import build_jarvis_context
from services.jarvis_tools import READ_ONLY_TOOLS, run_read_only_tool


def test_expected_agent_routes() -> None:
    assert determine_agents("How are sales doing?") == ["sales"]
    assert determine_agents("Can we afford another therapist?") == [
        "finance",
        "hr",
        "operations",
    ]
    assert determine_agents("Why are bookings falling?") == [
        "marketing",
        "operations",
        "customer_success",
    ]
    complete = determine_agents("Give me a complete business review.")
    assert complete == [
        "sales",
        "marketing",
        "finance",
        "operations",
        "hr",
        "customer_success",
        "analytics",
    ]


def test_booking_decline_routes_cross_functionally() -> None:
    agents = determine_agents(
        "Why are bookings falling? Review marketing and patient retention."
    )
    assert agents == ["marketing", "operations", "customer_success"]


def test_all_tools_are_read_only_and_privacy_safe() -> None:
    context = build_jarvis_context()
    serialized = []
    for name in READ_ONLY_TOOLS:
        result = run_read_only_tool(name, context)
        serialized.append(str(result).lower())
    combined = " ".join(serialized)
    assert "patient_name" not in combined
    assert "phone" not in combined
    assert "business_email" not in combined
    assert "clinical_notes" not in combined


def test_recorded_preferences_are_not_runtime_permissions() -> None:
    business = (
        build_jarvis_context()["model_context"]["confirmed_facts"]["business"]
    )
    assert "permissions" not in business
    assert "platforms" not in business
    assert business["runtime_authorization_status"].startswith(
        "Read-only consultation only"
    )


def test_orchestration_returns_a_read_only_trace() -> None:
    from unittest.mock import patch

    from services.specialist_orchestration import coordinate_specialists

    with patch(
        "services.specialist_orchestration.generate_ai_response",
        return_value="Evidence-based test response.",
    ):
        result = coordinate_specialists(
            "Can we afford another therapist?"
        )
    assert result["success"] is True
    assert result["agents"] == ["finance", "hr", "operations"]
    assert result["trace"]["mode"] == "read_only"
    assert result["trace"]["external_actions_executed"] is False
    assert result["trace"]["selected_agents"] == [
        "Finance Agent",
        "HR Agent",
        "Operations Agent",
    ]


if __name__ == "__main__":
    test_expected_agent_routes()
    test_booking_decline_routes_cross_functionally()
    test_all_tools_are_read_only_and_privacy_safe()
    test_recorded_preferences_are_not_runtime_permissions()
    test_orchestration_returns_a_read_only_trace()
    print("Specialist orchestration tests passed.")
