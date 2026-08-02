"""Regression test for the optimistic-locking gap: a raw save_memory()
call must bump the version column, or a concurrent update_memory() that
already read the row won't detect the raw write and will silently
overwrite it.

Runs entirely against a temporary local store, never the real database.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
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

            # --- the actual race: update_memory() must not lose a raw write
            #     that landed after its read but before its save ----------
            # First prove the low-level primitive rejects a stale version:
            # a caller holding an old (memory, version) snapshot must not be
            # able to save over a row that changed since it read.
            stale_memory, stale_version = business_memory.load_memory_versioned()
            business_memory.save_memory({**stale_memory, "reports": ["concurrent raw write"]})
            stale_memory["reports"].append("stale mutation")
            saved = business_memory.save_memory_versioned(stale_memory, stale_version)
            assert saved is False, (
                "save_memory_versioned() succeeded against a stale version — "
                "the raw save that happened in between was not detected"
            )

            # Now prove the high-level update_memory() actually lives up to
            # that guarantee end-to-end: inject a raw save from inside the
            # mutator itself (the exact window between update_memory()'s own
            # read and its save) and confirm it transparently retries instead
            # of clobbering the concurrent write.
            attempts = []

            def mutate(memory):
                attempts.append(1)
                if len(attempts) == 1:
                    business_memory.save_memory({**memory, "reports": ["concurrent raw write 2"]})
                memory.setdefault("tasks", []).append({"id": "T-1", "data": {"title": "via update_memory"}})

            business_memory.update_memory(mutate)
            assert len(attempts) >= 2, "update_memory() should have retried after the conflict"

            final = business_memory.load_memory()
            assert final["reports"] == ["concurrent raw write 2"], (
                "the concurrent raw write was lost"
            )
            assert any(t["data"]["title"] == "via update_memory" for t in final["tasks"]), (
                "update_memory()'s own change was lost"
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
