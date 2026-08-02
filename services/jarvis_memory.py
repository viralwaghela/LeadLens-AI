"""Structured, privacy-safe learning memory for Jarvis.

The business database remains the source of operational truth. This module
stores only owner preferences, tracked recommendations, measured outcomes and
derived patterns. Writes are explicit and atomic; normal Jarvis consultations
are read-only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "learning" / "learning_memory.json"
SCHEMA_VERSION = 3
_LOCK = threading.RLock()

DEFAULT_MEMORY: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "preferences": [],
    "recommendations": [],
    "outcomes": [],
    "executions": [],
    "patterns": [],
    "updated_at": "",
}

ALLOWED_RESULTS = {"successful", "partial", "unsuccessful", "unknown"}
TOKEN_PATTERN = re.compile(r"[a-z0-9₹%]+", re.IGNORECASE)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fresh_memory() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_MEMORY)


def _clean_text(value: Any, limit: int = 2000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _tokens(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_PATTERN.findall(_clean_text(value, 4000))
        if len(token) > 2
    }


def _migrate(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    migrated = _fresh_memory()
    for key in (
        "preferences",
        "recommendations",
        "outcomes",
        "executions",
        "patterns",
    ):
        rows = data.get(key, [])
        migrated[key] = [
            row for row in rows if isinstance(row, dict)
        ] if isinstance(rows, list) else []
    migrated["updated_at"] = _clean_text(data.get("updated_at"), 40)
    return migrated


def load_learning_memory() -> dict[str, Any]:
    with _LOCK:
        if not STORE.exists():
            return _fresh_memory()
        try:
            return _migrate(json.loads(STORE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return _fresh_memory()


def _save_learning_memory(data: dict[str, Any]) -> None:
    with _LOCK:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        data = _migrate(data)
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = _now()
        handle, temporary = tempfile.mkstemp(
            prefix="jarvis_memory_",
            suffix=".json",
            dir=STORE.parent,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, STORE)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


def save_owner_preference(
    key: str,
    value: Any,
    reason: str = "",
    source: str = "owner",
) -> dict[str, Any]:
    """Create or update an explicit owner preference."""
    clean_key = _clean_text(key, 100)
    if not clean_key:
        raise ValueError("Preference key is required.")
    row_value = value if isinstance(value, (bool, int, float)) else _clean_text(value)
    data = load_learning_memory()
    row = next(
        (
            item for item in data["preferences"]
            if str(item.get("key", "")).casefold() == clean_key.casefold()
        ),
        None,
    )
    if row is None:
        row = {
            "id": f"PREF-{uuid4().hex[:10].upper()}",
            "key": clean_key,
            "created_at": _now(),
        }
        data["preferences"].append(row)
    row.update({
        "value": row_value,
        "reason": _clean_text(reason, 500),
        "source": _clean_text(source, 60) or "owner",
        "updated_at": _now(),
        "active": True,
    })
    _save_learning_memory(data)
    return copy.deepcopy(row)


def track_recommendation(
    question: str,
    recommendation: str,
    agents: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Persist a Jarvis recommendation after explicit owner confirmation."""
    clean_question = _clean_text(question, 800)
    clean_recommendation = _clean_text(recommendation, 4000)
    if not clean_recommendation:
        raise ValueError("Recommendation text is required.")
    fingerprint = hashlib.sha256(
        f"{clean_question}|{clean_recommendation}".casefold().encode("utf-8")
    ).hexdigest()[:16]
    data = load_learning_memory()
    existing = next(
        (
            row for row in data["recommendations"]
            if row.get("fingerprint") == fingerprint
        ),
        None,
    )
    if existing:
        return copy.deepcopy(existing)
    row = {
        "id": f"REC-{uuid4().hex[:10].upper()}",
        "question": clean_question,
        "recommendation": clean_recommendation,
        "agents": sorted({
            _clean_text(agent, 80)
            for agent in (agents or [])
            if _clean_text(agent, 80)
        }),
        "tags": sorted({
            _clean_text(tag, 60).casefold()
            for tag in (tags or [])
            if _clean_text(tag, 60)
        }),
        "status": "tracked",
        "fingerprint": fingerprint,
        "created_at": _now(),
        "updated_at": _now(),
    }
    data["recommendations"].append(row)
    _save_learning_memory(data)
    return copy.deepcopy(row)


def _derive_patterns(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in outcomes:
        key = _clean_text(row.get("action_type"), 100) or "General"
        groups.setdefault(key, []).append(row)
    patterns: list[dict[str, Any]] = []
    for action_type, rows in groups.items():
        rated = [
            row for row in rows
            if row.get("result") in {"successful", "partial", "unsuccessful"}
        ]
        successes = sum(row.get("result") == "successful" for row in rated)
        partials = sum(row.get("result") == "partial" for row in rated)
        score = (
            (successes + 0.5 * partials) / len(rated)
            if rated else None
        )
        patterns.append({
            "action_type": action_type,
            "measured_runs": len(rated),
            "success_rate_percent": (
                round(score * 100, 1) if score is not None else None
            ),
            "confidence": (
                "High" if len(rated) >= 5
                else "Medium" if len(rated) >= 3
                else "Low"
            ),
            "last_measured_at": rows[-1].get("recorded_at"),
        })
    return sorted(
        patterns,
        key=lambda item: (
            item["measured_runs"],
            item["success_rate_percent"] or -1,
        ),
        reverse=True,
    )


def record_recommendation_outcome(
    recommendation_id: str,
    result: str,
    action_taken: str,
    metrics: dict[str, Any] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Record a measured result linked to a tracked recommendation."""
    clean_result = _clean_text(result, 30).casefold()
    if clean_result not in ALLOWED_RESULTS:
        raise ValueError(
            "Result must be successful, partial, unsuccessful or unknown."
        )
    data = load_learning_memory()
    recommendation = next(
        (
            row for row in data["recommendations"]
            if row.get("id") == recommendation_id
        ),
        None,
    )
    if recommendation is None:
        raise ValueError("Tracked recommendation was not found.")
    clean_metrics = {
        _clean_text(key, 80): value
        for key, value in (metrics or {}).items()
        if _clean_text(key, 80)
        and isinstance(value, (str, int, float, bool))
    }
    row = {
        "id": f"OUT-{uuid4().hex[:10].upper()}",
        "recommendation_id": recommendation_id,
        "action_type": (
            recommendation.get("tags", ["General"])[0]
            if recommendation.get("tags") else "General"
        ),
        "result": clean_result,
        "action_taken": _clean_text(action_taken, 1500),
        "metrics": clean_metrics,
        "notes": _clean_text(notes, 1500),
        "recorded_at": _now(),
    }
    data["outcomes"].append(row)
    recommendation["status"] = "measured"
    recommendation["latest_result"] = clean_result
    recommendation["updated_at"] = _now()
    data["patterns"] = _derive_patterns(data["outcomes"])
    _save_learning_memory(data)
    return copy.deepcopy(row)


def record_action_execution(
    recommendation_id: str,
    execution_id: str,
    provider: str,
    action: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store verified execution evidence without claiming business impact."""
    clean_execution_id = _clean_text(execution_id, 40)
    if not clean_execution_id:
        raise ValueError("Execution ID is required.")
    data = load_learning_memory()
    existing = next(
        (
            row for row in data["executions"]
            if row.get("execution_id") == clean_execution_id
        ),
        None,
    )
    if existing:
        return copy.deepcopy(existing)
    row = {
        "id": f"RUN-{uuid4().hex[:10].upper()}",
        "recommendation_id": _clean_text(recommendation_id, 40),
        "execution_id": clean_execution_id,
        "provider": _clean_text(provider, 40).casefold(),
        "action": _clean_text(action, 60).casefold(),
        "status": _clean_text(status, 30),
        "result": {
            _clean_text(key, 80): value
            for key, value in (result or {}).items()
            if _clean_text(key, 80)
            and isinstance(value, (str, int, float, bool))
        },
        "recorded_at": _now(),
    }
    data["executions"].append(row)
    recommendation = next(
        (
            item for item in data["recommendations"]
            if item.get("id") == row["recommendation_id"]
        ),
        None,
    )
    if recommendation:
        recommendation.setdefault("execution_ids", [])
        if clean_execution_id not in recommendation["execution_ids"]:
            recommendation["execution_ids"].append(clean_execution_id)
        recommendation["status"] = "action_executed"
        recommendation["updated_at"] = _now()
    _save_learning_memory(data)
    return copy.deepcopy(row)


def relevant_memory(query: str = "", limit: int = 8) -> dict[str, Any]:
    """Return a compact, ranked memory slice suitable for an LLM prompt."""
    data = load_learning_memory()
    query_tokens = _tokens(query)

    def ranked(rows: list[dict[str, Any]], text_keys: tuple[str, ...]) -> list[dict[str, Any]]:
        scored = []
        for index, row in enumerate(rows):
            corpus = " ".join(str(row.get(key, "")) for key in text_keys)
            overlap = len(query_tokens & _tokens(corpus))
            scored.append((overlap, index, row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [copy.deepcopy(item[2]) for item in scored[:limit]]

    preferences = [
        {
            "key": row.get("key"),
            "value": row.get("value"),
            "reason": row.get("reason"),
            "source": row.get("source"),
            "updated_at": row.get("updated_at"),
        }
        for row in data["preferences"]
        if row.get("active", True)
    ][-limit:]
    recommendations = ranked(
        data["recommendations"],
        ("question", "recommendation", "tags"),
    )
    outcomes = ranked(
        data["outcomes"],
        ("action_type", "action_taken", "notes", "result"),
    )
    executions = ranked(
        data["executions"],
        ("provider", "action", "status", "recommendation_id"),
    )
    return {
        "preferences": preferences,
        "relevant_recommendations": recommendations,
        "relevant_outcomes": outcomes,
        "recent_executions": executions,
        "patterns": copy.deepcopy(data["patterns"][:limit]),
        "counts": {
            "preferences": len(data["preferences"]),
            "recommendations": len(data["recommendations"]),
            "outcomes": len(data["outcomes"]),
            "executions": len(data["executions"]),
        },
        "updated_at": data.get("updated_at", ""),
    }


def memory_summary() -> dict[str, Any]:
    data = load_learning_memory()
    return {
        "preferences": data["preferences"],
        "recommendations": data["recommendations"],
        "outcomes": data["outcomes"],
        "executions": data["executions"],
        "patterns": data["patterns"],
        "updated_at": data.get("updated_at", ""),
    }
