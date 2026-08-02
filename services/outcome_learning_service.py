"""Compatibility interface for measured business outcomes."""
from services.jarvis_memory import (
    load_learning_memory,
    record_recommendation_outcome,
    track_recommendation,
)


def record_outcome(
    recommendation,
    action_taken,
    contacts,
    conversions,
    revenue_recovered,
    notes="",
):
    tracked = track_recommendation(
        "Measure an earlier recommendation",
        recommendation,
        tags=["Measured Outcome"],
    )
    result = (
        "successful" if int(conversions or 0) > 0
        else "unsuccessful"
    )
    return record_recommendation_outcome(
        tracked["id"],
        result,
        action_taken,
        metrics={
            "contacts": int(contacts or 0),
            "conversions": int(conversions or 0),
            "conversion_rate_percent": (
                round(int(conversions or 0) / int(contacts or 0) * 100, 1)
                if int(contacts or 0) else 0
            ),
            "revenue_recovered": float(revenue_recovered or 0),
        },
        notes=notes,
    )


def outcome_summary():
    rows = load_learning_memory().get("outcomes", [])
    metrics = [row.get("metrics", {}) for row in rows]
    return {
        "count": len(rows),
        "revenue": sum(
            float(item.get("revenue_recovered", 0) or 0)
            for item in metrics
        ),
        "contacts": sum(int(item.get("contacts", 0) or 0) for item in metrics),
        "conversions": sum(
            int(item.get("conversions", 0) or 0) for item in metrics
        ),
        "rows": rows[-20:],
    }
