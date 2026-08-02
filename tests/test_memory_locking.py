"""Regression tests for core.memory's locking guarantees:

1. A raw save_memory() call must bump the version column, or it would be
   invisible to anything checking versions.
2. update_memory() must not lose either writer's change when two callers
   genuinely race — it holds one connection/transaction across its own
   read and write (SELECT ... FOR UPDATE on Postgres, BEGIN IMMEDIATE on
   SQLite), so a second concurrent caller waits for the row lock rather
   than racing and losing data. This replaced an earlier two-connection,
   optimistic-version-check design after real production testing showed
   its connection churn was itself implicated in reproducible stale
   reads — see docs/AUTOMATION_ROADMAP.md and core.memory.update_memory's
   docstring for the full writeup. Because the new design is a real lock
   (not a check-and-retry), this must be tested with genuine concurrency
   (two threads) rather than by nesting a call inside the mutator — that
   would just deadlock the mutator against its own outer lock.

Runs entirely against a temporary local store, never the real database.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
import threading
import time
from pathlib import Path

import core.memory as business_memory


def run_tests() -> None:
    original_database_folder = business_memory.DATABASE_FOLDER

    try:
        with tempfile.TemporaryDirectory() as temporary:
            business_memory.DATABASE_FOLDER = Path(temporary) / "database"

            # --- raw save_memory() must bump version, not reset/ignore it ---
            memory, version_1 = business_memory.load_memory_versioned()
            assert version_1 == 1

            memory["reports"] = ["first raw write"]
            business_memory.save_memory(memory)

            _, version_2 = business_memory.load_memory_versioned()
            assert version_2 == version_1 + 1, (
                f"expected version to increment to {version_1 + 1}, got {version_2} — "
                "a raw save_memory() call is not bumping version"
            )

            memory["reports"] = ["second raw write"]
            business_memory.save_memory(memory)
            _, version_3 = business_memory.load_memory_versioned()
            assert version_3 == version_2 + 1

            # --- the low-level primitive rejects a stale version: a caller
            #     holding an old (memory, version) snapshot must not be able
            #     to save over a row that changed since it read. This still
            #     applies — load_memory_versioned/save_memory_versioned are
            #     unchanged, just no longer what update_memory() itself uses
            #     internally. ------------------------------------------------
            stale_memory, stale_version = business_memory.load_memory_versioned()
            business_memory.save_memory({**stale_memory, "reports": ["concurrent raw write"]})
            stale_memory["reports"].append("stale mutation")
            saved = business_memory.save_memory_versioned(stale_memory, stale_version)
            assert saved is False, (
                "save_memory_versioned() succeeded against a stale version — "
                "the raw save that happened in between was not detected"
            )

            # --- the actual race: two REAL concurrent update_memory() calls
            #     must both survive intact. update_memory() now holds a row
            #     lock across its own read and write, so the second caller
            #     should simply wait for the first to finish rather than
            #     racing and losing data — verified with genuine threads,
            #     synchronized via a barrier so both hit the lock at nearly
            #     the same instant. -----------------------------------------
            barrier = threading.Barrier(2)
            results: dict[str, bool] = {}
            errors: dict[str, BaseException] = {}

            def writer_a():
                def mutate(memory):
                    memory.setdefault("reports", []).append("writer A")
                    time.sleep(0.3)  # hold the lock a moment, so B must genuinely wait
                try:
                    barrier.wait(timeout=5)
                    business_memory.update_memory(mutate)
                    results["a"] = True
                except BaseException as error:  # noqa: BLE001
                    errors["a"] = error

            def writer_b():
                def mutate(memory):
                    memory.setdefault("tasks", []).append({"id": "T-B", "data": {"title": "writer B"}})
                try:
                    barrier.wait(timeout=5)
                    business_memory.update_memory(mutate)
                    results["b"] = True
                except BaseException as error:  # noqa: BLE001
                    errors["b"] = error

            thread_a = threading.Thread(target=writer_a)
            thread_b = threading.Thread(target=writer_b)
            thread_a.start()
            thread_b.start()
            thread_a.join(timeout=10)
            thread_b.join(timeout=10)

            assert not errors, f"concurrent writer(s) raised: {errors}"
            assert results.get("a") and results.get("b"), "both concurrent writers should complete"

            final = business_memory.load_memory()
            assert "writer A" in final.get("reports", []), "writer A's change was lost"
            assert any(t["data"]["title"] == "writer B" for t in final.get("tasks", [])), (
                "writer B's change was lost"
            )

            # --- reset_company() now goes through update_memory() too -------
            business_memory.update_company("business_name", "Test Clinic")
            before_reset, version_before_reset = business_memory.load_memory_versioned()
            assert before_reset["company"]["business_name"] == "Test Clinic"

            business_memory.reset_company()
            after_reset, version_after_reset = business_memory.load_memory_versioned()
            assert after_reset["company"] == {}
            assert version_after_reset == version_before_reset + 1, (
                "reset_company() should bump version by going through update_memory(), "
                "not silently reset/skip it"
            )

    finally:
        business_memory.DATABASE_FOLDER = original_database_folder


if __name__ == "__main__":
    run_tests()
    print("Memory locking tests passed.")
