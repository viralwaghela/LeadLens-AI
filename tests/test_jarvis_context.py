"""Regression checks for Jarvis context grounding and privacy."""
from __future__ import annotations

from services.jarvis_context import build_jarvis_context, context_audit


def test_context_has_provenance() -> None:
    context = build_jarvis_context()
    model_context = context["model_context"]
    sources = {
        item["source"]
        for item in model_context["source_register"]
    }
    assert "business.memory" in sources
    assert "clinic.patients" in sources
    assert "clinic.therapists" in sources
    assert "learning.memory" in sources


def test_context_excludes_direct_identifiers() -> None:
    model_context = build_jarvis_context()["model_context"]
    keys: set[str] = set()

    def collect_keys(value) -> None:
        if isinstance(value, dict):
            keys.update(str(key).lower() for key in value)
            for child in value.values():
                collect_keys(child)
        elif isinstance(value, list):
            for child in value:
                collect_keys(child)

    collect_keys(model_context)
    forbidden = {
        "patient_name",
        "therapist_name",
        "phone",
        "email",
        "clinical_notes",
        "diagnosis",
    }
    assert forbidden.isdisjoint(keys)


def test_context_audit_is_consistent() -> None:
    audit = context_audit()
    assert audit["sources_loaded"] <= audit["sources_total"]
    assert audit["privacy_mode"] == "aggregate_only"


if __name__ == "__main__":
    test_context_has_provenance()
    test_context_excludes_direct_identifiers()
    test_context_audit_is_consistent()
    print("Jarvis context tests passed.")
