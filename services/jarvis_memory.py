"""Structured, privacy-safe learning memory for Jarvis.

The business database remains the source of operational truth. This module
stores only owner preferences, tracked recommendations, measured outcomes and
derived patterns. Writes are explicit and atomic; normal Jarvis consultations
are read-only.

Phase 2 storage design (see docs/V2_PHASE2_JARVIS_MEMORY.md for the full
writeup):

    read:  DB (core/db/models/jarvis.py's JarvisLearningRecord)
           -> if no migrated data exists for the default organization yet,
              or the DB is unavailable, fall back to the legacy JSON file

    write: legacy JSON file (STORE), always, exactly as before Phase 2
           -> then best-effort mirror into the DB

The legacy JSON write is NOT a "during transition only" measure — it is
permanent, because services/jarvis_context.py has its own independent
direct-file-read on this exact path (for a provenance/source-status
display) that Phase 2 deliberately does not touch, per its "storage
migration only" scope. See that file's LEARNING_FILE constant.

Every public function's signature and return shape is unchanged from
before Phase 2 — callers do not need to know or care whether a given
read came from the database or the JSON file.

DB failures never crash a Jarvis write: the JSON write happens first and
unconditionally, so a database outage never loses data or corrupts the
legacy file; the DB write is attempted afterward, and a failure there is
logged loudly (not swallowed) rather than silently reported as success.
An explicit environment-variable kill switch
(LEADLENS_JARVIS_MEMORY_DB_DISABLED=1) restores pre-Phase-2, JSON-only
behavior entirely, for rollback — see docs/V2_PHASE2_JARVIS_MEMORY.md.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.models.jarvis import JarvisLearningRecord, JarvisLearningRecordType
from core.db.session import make_engine, session_scope
from core.identity.organization_service import create_organization, get_organization_by_slug


ROOT = Path(__file__).resolve().parents[1]


def _resolve_store_path() -> Path:
    override = os.getenv("LEADLENS_LEARNING_MEMORY_PATH", "").strip()
    if override:
        return Path(override)
    return ROOT / "data" / "learning" / "learning_memory.json"


STORE = _resolve_store_path()
SCHEMA_VERSION = 3
_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)

# Rollback kill switch (see module docstring / docs/V2_PHASE2_JARVIS_MEMORY.md):
# when set, every DB read/write path is skipped entirely and this module
# behaves exactly as it did before Phase 2 (JSON file only).
_DB_DISABLED = os.getenv("LEADLENS_JARVIS_MEMORY_DB_DISABLED", "").strip().lower() in {
    "1", "true", "yes",
}

# Single-clinic bootstrap default. Not live tenant routing — there is no
# per-request/per-user organization context anywhere in the live app yet
# (see CLAUDE.md's multi-tenancy gap) — this is only the one fixed
# organization this Phase 2 storage layer writes to today, resolved
# fresh (get-or-create by slug) on every DB access rather than cached,
# so tests can freely swap engines without stale-ID bugs.
DEFAULT_ORGANIZATION_SLUG = os.getenv("LEADLENS_DEFAULT_ORG_SLUG", "default-clinic")
DEFAULT_ORGANIZATION_NAME = "Default Clinic"

# Lazily created, cached engine. Tests override this directly
# (patch.object(jarvis_memory, "_ENGINE", test_engine)), exactly like the
# existing STORE-patching convention — see tests/test_phase2_jarvis_memory.py.
_ENGINE = None
_ENGINE_LOCK = threading.RLock()

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

_RECORD_TYPE_BY_KEY = {
    "preferences": JarvisLearningRecordType.PREFERENCE,
    "recommendations": JarvisLearningRecordType.RECOMMENDATION,
    "outcomes": JarvisLearningRecordType.OUTCOME,
    "executions": JarvisLearningRecordType.EXECUTION,
}
_AUTHORED_KEYS = tuple(_RECORD_TYPE_BY_KEY.keys())  # excludes "patterns" — always derived


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


# ---------------------------------------------------------------------------
# DB engine / default-organization resolution
# ---------------------------------------------------------------------------

def _get_engine():
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = make_engine()
        return _ENGINE


def _resolve_default_organization_id(session: Session) -> int:
    """Get-or-create the single default organization this deployment's
    Jarvis memory is scoped to. Resolved fresh every call (not cached)
    so tests can swap engines without stale-ID bugs — this deployment
    has at most a handful of Jarvis-memory DB calls per user action, so
    the extra SELECT is not a meaningful cost. See module docstring for
    why this is bootstrap plumbing, not live tenant routing."""
    org = get_organization_by_slug(session, DEFAULT_ORGANIZATION_SLUG)
    if org is not None:
        return org.id
    org = create_organization(
        session, name=DEFAULT_ORGANIZATION_NAME, slug=DEFAULT_ORGANIZATION_SLUG
    )
    return org.id


# ---------------------------------------------------------------------------
# JSON storage (legacy path — always written, sometimes read)
# ---------------------------------------------------------------------------

def _load_from_json_only() -> dict[str, Any]:
    with _LOCK:
        if not STORE.exists():
            return _fresh_memory()
        try:
            return _migrate(json.loads(STORE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return _fresh_memory()


def _write_json_compat(data: dict[str, Any]) -> None:
    """Atomic write to the legacy JSON file. Always called on every save,
    permanently (see module docstring) — this is not a transitional
    measure."""
    with _LOCK:
        STORE.parent.mkdir(parents=True, exist_ok=True)
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


# ---------------------------------------------------------------------------
# DB storage (Phase 2 primary path, once migrated data exists)
# ---------------------------------------------------------------------------

def _row_fingerprint(row: dict[str, Any]) -> str:
    """Every legacy row already carries its own stable, unique `id`
    (e.g. "PREF-A1B2C3D4E5") — reused directly as the DB dedup key
    rather than inventing a second identifier scheme."""
    row_id = _clean_text(row.get("id"), 64)
    if row_id:
        return row_id
    # Defensive fallback for a malformed/legacy row with no id — should
    # not happen via this module's own writers, but a hand-edited JSON
    # file could lack one.
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _load_from_db() -> dict[str, Any] | None:
    """Returns the full memory dict from the DB, or None if this
    organization has no migrated data yet (signaling the caller should
    fall back to the legacy JSON file)."""
    engine = _get_engine()
    with session_scope(engine) as session:
        org_id = _resolve_default_organization_id(session)
        by_key: dict[str, list[dict[str, Any]]] = {key: [] for key in _AUTHORED_KEYS}
        latest_updated_at: datetime | None = None
        total_rows = 0
        for key, record_type in _RECORD_TYPE_BY_KEY.items():
            rows = (
                session.query(JarvisLearningRecord)
                .filter(
                    JarvisLearningRecord.organization_id == org_id,
                    JarvisLearningRecord.record_type == record_type,
                )
                .order_by(JarvisLearningRecord.created_at.asc())
                .all()
            )
            total_rows += len(rows)
            for db_row in rows:
                try:
                    by_key[key].append(json.loads(db_row.payload))
                except json.JSONDecodeError:
                    continue
                if latest_updated_at is None or db_row.updated_at > latest_updated_at:
                    latest_updated_at = db_row.updated_at

        if total_rows == 0:
            return None  # not migrated yet — caller falls back to JSON

        data = _fresh_memory()
        for key in _AUTHORED_KEYS:
            data[key] = by_key[key]
        data["patterns"] = _derive_patterns(data["outcomes"])
        data["updated_at"] = (
            latest_updated_at.isoformat(timespec="seconds") if latest_updated_at else ""
        )
        return data


def _write_to_db(data: dict[str, Any]) -> None:
    """Full-sync upsert of all four authored collections for the default
    organization. Simple (re-upserts every row on every save) rather
    than diffing for the one row that actually changed — deliberately,
    for correctness and simplicity at this deployment's scale (a single
    clinic's Jarvis memory: dozens to low hundreds of rows, not
    thousands). See docs/V2_PHASE2_JARVIS_MEMORY.md for this tradeoff if
    usage ever grows large enough to matter."""
    engine = _get_engine()
    with session_scope(engine) as session:
        org_id = _resolve_default_organization_id(session)
        now = datetime.now(timezone.utc)
        for key, record_type in _RECORD_TYPE_BY_KEY.items():
            for row in data.get(key, []):
                if not isinstance(row, dict):
                    continue
                fingerprint = _row_fingerprint(row)
                existing = (
                    session.query(JarvisLearningRecord)
                    .filter(
                        JarvisLearningRecord.organization_id == org_id,
                        JarvisLearningRecord.record_type == record_type,
                        JarvisLearningRecord.fingerprint == fingerprint,
                    )
                    .one_or_none()
                )
                payload = json.dumps(row, ensure_ascii=False, default=str)
                if existing is not None:
                    if existing.payload != payload:
                        existing.payload = payload
                        existing.updated_at = now
                else:
                    session.add(
                        JarvisLearningRecord(
                            organization_id=org_id,
                            record_type=record_type,
                            external_id=_clean_text(row.get("id"), 60) or None,
                            fingerprint=fingerprint,
                            payload=payload,
                            created_at=now,
                            updated_at=now,
                        )
                    )


# ---------------------------------------------------------------------------
# Public read/write chokepoints
# ---------------------------------------------------------------------------

def load_learning_memory() -> dict[str, Any]:
    if not _DB_DISABLED:
        try:
            db_data = _load_from_db()
            if db_data is not None:
                return db_data
        except SQLAlchemyError:
            _LOG.error(
                "Jarvis learning-memory DB read failed; falling back to the "
                "legacy JSON file (%s).",
                STORE,
                exc_info=True,
            )
    return _load_from_json_only()


def _save_learning_memory(data: dict[str, Any]) -> None:
    with _LOCK:
        data = _migrate(data)
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = _now()
        # Always written first and unconditionally: this is the
        # durability floor Jarvis has always had, and
        # services/jarvis_context.py's LEARNING_FILE reads it directly —
        # see module docstring.
        _write_json_compat(data)
        if not _DB_DISABLED:
            try:
                _write_to_db(data)
            except SQLAlchemyError:
                _LOG.error(
                    "Jarvis learning-memory DB write failed; the legacy "
                    "JSON file (%s) is up to date, but this write was not "
                    "durably mirrored to the database.",
                    STORE,
                    exc_info=True,
                )


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
