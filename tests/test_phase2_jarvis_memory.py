"""V2 Phase 2 tests: Jarvis learning-memory's database-backed storage.

Every test uses its own private, temporary SQLite database and JSON
file (never the tracked data/learning/learning_memory.json, never a
real DATABASE_URL) — see the `isolated` fixture below and
tests/_bootstrap.py's own process-wide safety net for defense in depth.
import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.jarvis import JarvisLearningRecord, JarvisLearningRecordType
from core.db.session import make_engine, session_scope
from core.identity import organization_service

import services.jarvis_memory as jm
import scripts.migrate_jarvis_memory_to_db as migrate_mod


@pytest.fixture()
def isolated(tmp_path):
    """Fresh in-memory DB (schema created) + fresh temp JSON path, both
    monkeypatched onto jarvis_memory for the duration of one test."""
    store = tmp_path / "learning_memory.json"
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with patch.object(jm, "STORE", store), patch.object(jm, "_ENGINE", engine), \
         patch.object(migrate_mod, "DEFAULT_STORE", store):
        yield store, engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Core read/write behavior
# ---------------------------------------------------------------------------

def test_empty_memory_returns_fresh_structure(isolated) -> None:
    data = jm.load_learning_memory()
    assert data["preferences"] == []
    assert data["recommendations"] == []
    assert data["outcomes"] == []
    assert data["executions"] == []
    assert data["patterns"] == []
    assert data["schema_version"] == jm.SCHEMA_VERSION


def test_write_then_read_preference(isolated) -> None:
    saved = jm.save_owner_preference("approval_required", True, "test reason")
    data = jm.load_learning_memory()
    assert len(data["preferences"]) == 1
    assert data["preferences"][0]["id"] == saved["id"]
    assert data["preferences"][0]["value"] is True


def test_update_existing_preference_does_not_duplicate(isolated) -> None:
    first = jm.save_owner_preference("tone", "formal", "initial")
    second = jm.save_owner_preference("tone", "casual", "changed my mind")
    data = jm.load_learning_memory()
    assert len(data["preferences"]) == 1
    assert first["id"] == second["id"]
    assert data["preferences"][0]["value"] == "casual"


def test_track_recommendation_dedupes_by_fingerprint(isolated) -> None:
    a = jm.track_recommendation("How to grow leads?", "Run a referral campaign.")
    b = jm.track_recommendation("How to grow leads?", "Run a referral campaign.")
    data = jm.load_learning_memory()
    assert a["id"] == b["id"]
    assert len(data["recommendations"]) == 1


def test_record_outcome_updates_recommendation_and_derives_patterns(isolated) -> None:
    tracked = jm.track_recommendation("Improve renewals", "Call lapsed patients.")
    outcome = jm.record_recommendation_outcome(
        tracked["id"], "successful", "Called 10 patients.",
        metrics={"conversions": 4}, notes="Good response.",
    )
    data = jm.load_learning_memory()
    rec = next(r for r in data["recommendations"] if r["id"] == tracked["id"])
    assert rec["status"] == "measured"
    assert rec["latest_result"] == "successful"
    assert len(data["outcomes"]) == 1
    assert data["outcomes"][0]["id"] == outcome["id"]
    assert data["patterns"][0]["success_rate_percent"] == 100.0


def test_record_action_execution_dedupes_by_execution_id(isolated) -> None:
    tracked = jm.track_recommendation("q", "r")
    a = jm.record_action_execution(tracked["id"], "EXEC-1", "gmail", "create_draft", "sent")
    b = jm.record_action_execution(tracked["id"], "EXEC-1", "gmail", "create_draft", "sent")
    data = jm.load_learning_memory()
    assert a["id"] == b["id"]
    assert len(data["executions"]) == 1


# ---------------------------------------------------------------------------
# Durability / persistence across "reload"
# ---------------------------------------------------------------------------

def test_data_is_durably_persisted_in_db_not_just_in_process(isolated) -> None:
    store, engine = isolated
    saved = jm.save_owner_preference("channel", "whatsapp", "owner said so")

    # Query the DB directly with a brand-new session, bypassing
    # jarvis_memory's own read path entirely — proves the row is really
    # in durable storage, not held only in some in-process cache.
    with Session(engine) as fresh_session:
        row = (
            fresh_session.query(JarvisLearningRecord)
            .filter(JarvisLearningRecord.fingerprint == saved["id"])
            .one()
        )
        payload = json.loads(row.payload)
        assert payload["value"] == "whatsapp"

    # And a second, independent call to the public read API sees it too.
    reloaded = jm.load_learning_memory()
    assert any(p["id"] == saved["id"] for p in reloaded["preferences"])


def test_json_compatibility_file_is_kept_in_sync(isolated) -> None:
    """services/jarvis_context.py reads STORE directly for its
    provenance display — this proves that path keeps working."""
    store, engine = isolated
    jm.save_owner_preference("k", "v")
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert len(on_disk["preferences"]) == 1
    assert on_disk["schema_version"] == jm.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Migration from legacy JSON
# ---------------------------------------------------------------------------

def _write_legacy_json(path, **overrides) -> dict:
    data = {
        "schema_version": 3,
        "preferences": [{"id": "PREF-LEGACY001", "key": "k", "value": "v", "created_at": "2026-01-01T09:00:00", "updated_at": "2026-01-01T09:00:00", "active": True}],
        "recommendations": [{"id": "REC-LEGACY001", "question": "q", "recommendation": "r", "agents": [], "tags": [], "status": "tracked", "fingerprint": "fp1", "created_at": "2026-01-01T09:00:00", "updated_at": "2026-01-01T09:00:00"}],
        "outcomes": [],
        "executions": [],
        "patterns": [],
        "updated_at": "2026-01-01T09:00:00",
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_migration_from_legacy_json_populates_db(isolated) -> None:
    store, engine = isolated
    _write_legacy_json(store)

    report = migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)
    assert report["by_type"]["preferences"]["created"] == 1
    assert report["by_type"]["recommendations"]["created"] == 1

    with Session(engine) as session:
        org = organization_service.get_organization_by_slug(session, "mig-test")
        rows = session.query(JarvisLearningRecord).filter(
            JarvisLearningRecord.organization_id == org.id
        ).all()
        assert len(rows) == 2


def test_migration_is_idempotent(isolated) -> None:
    store, engine = isolated
    _write_legacy_json(store)

    migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)
    second = migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)

    assert second["by_type"]["preferences"]["created"] == 0
    assert second["by_type"]["preferences"]["already_present"] == 1
    assert second["by_type"]["recommendations"]["created"] == 0
    assert second["by_type"]["recommendations"]["already_present"] == 1

    with Session(engine) as session:
        org = organization_service.get_organization_by_slug(session, "mig-test")
        rows = session.query(JarvisLearningRecord).filter(
            JarvisLearningRecord.organization_id == org.id
        ).all()
        assert len(rows) == 2  # not duplicated


def test_migration_reports_malformed_json_without_crashing(isolated) -> None:
    store, engine = isolated
    store.write_text("{not valid json", encoding="utf-8")

    report = migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)
    assert report["error"] is not None
    assert "malformed" in report["error"].lower()


def test_migration_missing_file_reports_error_not_crash(isolated, tmp_path) -> None:
    missing = tmp_path / "does_not_exist.json"
    report = migrate_mod.migrate(json_path=missing, org_slug="mig-test", org_name="Mig Test")
    assert report["error"] is not None


def test_migration_never_overwrites_newer_db_record(isolated) -> None:
    store, engine = isolated
    _write_legacy_json(store)
    migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)

    # Simulate the DB row having since been changed by the live app
    # (i.e. it's now "newer" than the JSON snapshot).
    with Session(engine) as session:
        org = organization_service.get_organization_by_slug(session, "mig-test")
        row = session.query(JarvisLearningRecord).filter(
            JarvisLearningRecord.organization_id == org.id,
            JarvisLearningRecord.record_type == JarvisLearningRecordType.PREFERENCE,
        ).one()
        row.payload = json.dumps({"id": "PREF-LEGACY001", "key": "k", "value": "CHANGED_BY_LIVE_APP"})
        session.commit()

    report = migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)
    assert report["by_type"]["preferences"]["created"] == 0
    assert report["by_type"]["preferences"]["already_present"] == 1

    with Session(engine) as session:
        org = organization_service.get_organization_by_slug(session, "mig-test")
        row = session.query(JarvisLearningRecord).filter(
            JarvisLearningRecord.organization_id == org.id,
            JarvisLearningRecord.record_type == JarvisLearningRecordType.PREFERENCE,
        ).one()
        assert json.loads(row.payload)["value"] == "CHANGED_BY_LIVE_APP"


def test_verify_detects_hash_mismatch_between_json_and_db(isolated) -> None:
    store, engine = isolated
    _write_legacy_json(store)
    migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)

    with Session(engine) as session:
        org = organization_service.get_organization_by_slug(session, "mig-test")
        row = session.query(JarvisLearningRecord).filter(
            JarvisLearningRecord.organization_id == org.id,
            JarvisLearningRecord.record_type == JarvisLearningRecordType.PREFERENCE,
        ).one()
        row.payload = json.dumps({"id": "PREF-LEGACY001", "key": "k", "value": "DIVERGED"})
        session.commit()

    report = migrate_mod.verify(json_path=store, org_slug="mig-test", engine=engine)
    assert report["ok"] is False
    assert report["by_type"]["preferences"]["hash_mismatches"]


def test_verify_passes_when_json_and_db_agree(isolated) -> None:
    store, engine = isolated
    _write_legacy_json(store)
    migrate_mod.migrate(json_path=store, org_slug="mig-test", org_name="Mig Test", engine=engine)

    report = migrate_mod.verify(json_path=store, org_slug="mig-test", engine=engine)
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# Organization isolation (structural — schema-boundary only, matching
# Phase 0/1's own testing philosophy: proves the DB allows this, not that
# any live service routes tenants yet)
# ---------------------------------------------------------------------------

def test_organization_a_memory_isolated_from_organization_b(isolated) -> None:
    store, engine = isolated
    with Session(engine) as session:
        org_a = organization_service.create_organization(session, name="Org A", slug="org-a")
        org_b = organization_service.create_organization(session, name="Org B", slug="org-b")
        session.add(JarvisLearningRecord(
            organization_id=org_a.id, record_type=JarvisLearningRecordType.PREFERENCE,
            external_id="PREF-SAME", fingerprint="PREF-SAME",
            payload=json.dumps({"id": "PREF-SAME", "key": "k", "value": "for org A"}),
            created_at=jm.datetime.now(jm.timezone.utc), updated_at=jm.datetime.now(jm.timezone.utc),
        ))
        session.add(JarvisLearningRecord(
            organization_id=org_b.id, record_type=JarvisLearningRecordType.PREFERENCE,
            external_id="PREF-SAME", fingerprint="PREF-SAME",
            payload=json.dumps({"id": "PREF-SAME", "key": "k", "value": "for org B"}),
            created_at=jm.datetime.now(jm.timezone.utc), updated_at=jm.datetime.now(jm.timezone.utc),
        ))
        session.commit()  # must not raise — same fingerprint, different orgs

    with Session(engine) as session:
        rows = session.query(JarvisLearningRecord).filter(
            JarvisLearningRecord.fingerprint == "PREF-SAME"
        ).all()
        assert len(rows) == 2
        values = {json.loads(r.payload)["value"] for r in rows}
        assert values == {"for org A", "for org B"}


# ---------------------------------------------------------------------------
# DB failure handling — fail safe, never lose the write, never crash
# ---------------------------------------------------------------------------

def test_write_survives_db_being_unavailable(isolated, tmp_path) -> None:
    store, engine = isolated
    # A schema-less engine simulates "DB unavailable" (e.g. migrations
    # not yet run against it) — every query raises OperationalError,
    # a SQLAlchemyError subclass, which jarvis_memory.py must catch.
    broken_engine = make_engine("sqlite:///:memory:")  # no create_all()
    with patch.object(jm, "_ENGINE", broken_engine):
        saved = jm.save_owner_preference("k", "v", "test")  # must not raise
        assert saved["value"] == "v"
        on_disk = json.loads(store.read_text(encoding="utf-8"))
        assert on_disk["preferences"][0]["key"] == "k"
    broken_engine.dispose()


def test_read_falls_back_to_json_when_db_unavailable(isolated) -> None:
    store, engine = isolated
    _write_legacy_json(store)  # legacy data on disk, nothing in DB

    broken_engine = make_engine("sqlite:///:memory:")  # no create_all()
    with patch.object(jm, "_ENGINE", broken_engine):
        data = jm.load_learning_memory()  # must not raise
        assert len(data["preferences"]) == 1
        assert data["preferences"][0]["id"] == "PREF-LEGACY001"
    broken_engine.dispose()


def test_kill_switch_forces_json_only_behavior(isolated) -> None:
    store, engine = isolated
    with patch.object(jm, "_DB_DISABLED", True):
        saved = jm.save_owner_preference("k", "v")
        with Session(engine) as session:
            count = session.query(JarvisLearningRecord).count()
        assert count == 0  # DB never touched
        data = jm.load_learning_memory()
        assert data["preferences"][0]["id"] == saved["id"]  # JSON still works


# ---------------------------------------------------------------------------
# Legacy-JSON fallback boundary (before any migrated data exists)
# ---------------------------------------------------------------------------

def test_read_uses_legacy_json_when_no_db_data_migrated_yet(isolated) -> None:
    store, engine = isolated
    _write_legacy_json(store)  # DB is empty, JSON has data
    data = jm.load_learning_memory()
    assert len(data["preferences"]) == 1
    assert data["preferences"][0]["id"] == "PREF-LEGACY001"


def test_malformed_legacy_json_falls_back_to_empty_not_a_crash(isolated) -> None:
    store, engine = isolated
    store.write_text("{not valid json at all", encoding="utf-8")
    data = jm.load_learning_memory()  # must not raise
    assert data["preferences"] == []


# ---------------------------------------------------------------------------
# Public API compatibility (relevant_memory / memory_summary shape)
# ---------------------------------------------------------------------------

def test_relevant_memory_shape_unchanged(isolated) -> None:
    jm.save_owner_preference("channel", "whatsapp")
    tracked = jm.track_recommendation("renewal patients", "Call them.")
    jm.record_recommendation_outcome(tracked["id"], "successful", "Called.", metrics={"x": 1})

    relevant = jm.relevant_memory("renewal patients")
    assert set(relevant.keys()) == {
        "preferences", "relevant_recommendations", "relevant_outcomes",
        "recent_executions", "patterns", "counts", "updated_at",
    }
    assert relevant["counts"]["preferences"] == 1
    assert relevant["counts"]["recommendations"] == 1


def test_memory_summary_shape_unchanged(isolated) -> None:
    jm.save_owner_preference("k", "v")
    summary = jm.memory_summary()
    assert set(summary.keys()) == {
        "preferences", "recommendations", "outcomes", "executions", "patterns", "updated_at",
    }
