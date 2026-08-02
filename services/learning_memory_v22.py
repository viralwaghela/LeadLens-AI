"""Compatibility wrappers for the original Phase 22 learning interface."""
from __future__ import annotations

from typing import Any

from services.jarvis_memory import (
    load_learning_memory,
    memory_summary,
    save_owner_preference,
)


def record_learning_outcome(
    action_type: str,
    channel: str,
    audience: str,
    contacts: int,
    conversions: int,
    revenue: float,
    cost: float = 0,
    notes: str = "",
) -> dict[str, Any]:
    from services.jarvis_memory import track_recommendation, record_recommendation_outcome

    contacts = max(int(contacts or 0), 0)
    conversions = max(int(conversions or 0), 0)
    recommendation = track_recommendation(
        question=f"Measure {action_type}",
        recommendation=(
            f"{action_type} through {channel} for {audience}"
        ),
        tags=[action_type, channel],
    )
    roi = (
        ((float(revenue or 0) - float(cost or 0)) / float(cost) * 100)
        if float(cost or 0) > 0 else None
    )
    result = (
        "successful" if conversions > 0 and float(revenue or 0) >= float(cost or 0)
        else "partial" if conversions > 0
        else "unsuccessful"
    )
    outcome = record_recommendation_outcome(
        recommendation["id"],
        result,
        f"{action_type} via {channel}",
        metrics={
            "audience": audience,
            "contacts": contacts,
            "conversions": conversions,
            "conversion_rate_percent": (
                round(conversions / contacts * 100, 2) if contacts else 0
            ),
            "revenue": float(revenue or 0),
            "cost": float(cost or 0),
            "roi_percent": round(roi, 2) if roi is not None else "",
        },
        notes=notes,
    )
    row = {
        "id": outcome["id"],
        "action_type": action_type.strip() or "Unknown",
        "channel": channel.strip() or "Unknown",
        "audience": audience.strip() or "General",
        "contacts": contacts,
        "conversions": conversions,
        "conversion_rate": round(conversions / contacts * 100, 2) if contacts else 0,
        "revenue": float(revenue or 0),
        "cost": float(cost or 0),
        "roi_pct": round(roi, 2) if roi is not None else None,
        "notes": notes.strip(),
        "recorded_at": outcome["recorded_at"],
    }
    return row


def save_business_preference(key: str, value: str, reason: str = "") -> dict[str, Any]:
    return save_owner_preference(key, value, reason, source="owner")


def derive_patterns(outcomes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if outcomes is not None:
        # Legacy callers only need a readable list; stored patterns are the
        # canonical derived representation in the new memory service.
        return load_learning_memory().get("patterns", [])
    return load_learning_memory().get("patterns", [])


def recommendation_context() -> dict[str, Any]:
    data = load_learning_memory()
    patterns = data.get("patterns", [])
    return {
        "best_patterns": patterns[:5],
        "preferences": data["preferences"][-20:],
        "outcome_count": len(data["outcomes"]),
        "updated_at": data.get("updated_at", ""),
    }


def learning_summary() -> dict[str, Any]:
    return memory_summary()
