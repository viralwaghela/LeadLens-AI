import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from services import jarvis_memory


def run_tests():
    with tempfile.TemporaryDirectory() as folder:
        store = Path(folder) / "learning_memory.json"
        with patch.object(jarvis_memory, "STORE", store):
            preference = jarvis_memory.save_owner_preference(
                "approval_required",
                True,
                "Owner wants approval before external actions.",
            )
            assert preference["value"] is True

            tracked = jarvis_memory.track_recommendation(
                "How should we improve renewals?",
                "Call patients with one or two sessions remaining.",
                agents=["Customer Success Agent"],
                tags=["renewals"],
            )
            duplicate = jarvis_memory.track_recommendation(
                "How should we improve renewals?",
                "Call patients with one or two sessions remaining.",
                agents=["Customer Success Agent"],
                tags=["renewals"],
            )
            assert duplicate["id"] == tracked["id"]

            outcome = jarvis_memory.record_recommendation_outcome(
                tracked["id"],
                "successful",
                "Contacted the renewal cohort after approval.",
                metrics={"renewals": 4, "revenue_recovered": 12000},
                notes="Four packages renewed.",
            )
            assert outcome["recommendation_id"] == tracked["id"]

            persisted = json.loads(store.read_text(encoding="utf-8"))
            assert persisted["schema_version"] == 3
            assert len(persisted["recommendations"]) == 1
            assert len(persisted["outcomes"]) == 1
            assert persisted["executions"] == []
            assert persisted["patterns"][0]["success_rate_percent"] == 100.0

            relevant = jarvis_memory.relevant_memory("renewal patients")
            assert relevant["preferences"][0]["key"] == "approval_required"
            assert relevant["relevant_recommendations"][0]["id"] == tracked["id"]
            assert relevant["relevant_outcomes"][0]["id"] == outcome["id"]

    print("Jarvis memory tests passed.")


if __name__ == "__main__":
    run_tests()
