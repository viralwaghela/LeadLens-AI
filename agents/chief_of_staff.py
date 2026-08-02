
"""Dynamic Chief of Staff coordinator for LeadLens CareOS."""
from __future__ import annotations

from services.specialist_orchestration import coordinate_specialists


class ChiefOfStaff:
    """Answer management questions from the shared clinic memory."""

    def process_query(
        self,
        query: str,
        conversation_history: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        clean_query = str(query or "").strip()
        return coordinate_specialists(
            clean_query,
            conversation_history=conversation_history,
        )
