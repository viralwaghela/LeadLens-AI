"""Import this FIRST — before core.memory, scheduler, or anything that
might import them — in every test file under tests/.

Guarantees a test can never silently reach the real database, no matter
what happens after this import. Two distinct cases, handled differently
on purpose:

1. DATABASE_URL is already set in the process environment at the moment
   this module is imported (before any test code has run at all) — e.g.
   a shell that exported it, a CI runner with it configured, or a stray
   `DATABASE_URL=... python tests/foo.py`. This is treated as an error:
   raises immediately and loudly, rather than quietly popping it and
   proceeding. Silently "fixing" a contaminated environment would hide
   exactly the kind of mistake this module exists to catch.

2. DATABASE_URL gets (re-)populated *later*, during normal test
   execution, by the app's own code — e.g. scheduler.run_scheduled_checks
   calls load_dotenv() at its own import time, which reads the real
   DATABASE_URL out of .env. This is expected, ordinary behavior (that
   module needs load_dotenv() to work correctly outside of tests), so it
   is silently neutralized rather than treated as an error: this module
   monkeypatches dotenv.load_dotenv so every call, from anywhere, loads
   .env normally (other vars still work) but immediately strips
   DATABASE_URL back out.

Case 2 is why popping DATABASE_URL once, here, would not have been
enough on its own — that's exactly the setup that let
tests/test_low_booking_alert.py silently write real alerts into
production Postgres on 2026-08-02: it imported
scheduler.run_scheduled_checks, whose load_dotenv() call leaked the real
DATABASE_URL back in after the fact, and nothing caught it. The
monkeypatch closes that path. core.memory._database_url() also
hard-fails (LEADLENS_TESTING) if DATABASE_URL is ever set at the moment
something actually tries to use it — a second, independent layer in
case some future code path finds a way around the monkeypatch too.

3. (Added in Phase 2.) services/jarvis_memory.py is a genuinely live
   module — unlike everything else under core/db/ so far — and by
   default writes to data/learning/learning_memory.json (a tracked
   file) and, once migrated, to the V2 database. A test file that calls
   it without redirecting jarvis_memory.STORE itself (several did — see
   the Phase 2 audit that found tests/TEST_PHASES_21_TO_23.py silently
   mutating the tracked JSON file on every test run) would corrupt
   real, tracked runtime data or pollute the shared local V2 sqlite
   file. Rather than rely on every test file remembering to redirect
   both storage paths itself, this module sets two environment
   variables — LEADLENS_LEARNING_MEMORY_PATH and
   LEADLENS_V2_DATABASE_URL — to a private per-process temp directory
   before anything else can import jarvis_memory.py, so this class of
   mistake is structurally impossible rather than dependent on every
   test file getting it right. Individual test files may still
   monkeypatch jarvis_memory.STORE/_ENGINE directly for full per-test
   isolation (several do); this is a safety net underneath that, not a
   replacement for it.

Usage, as the very first line of a test file:

    import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)
"""
import atexit
import os
import shutil
import tempfile

import dotenv

os.environ["LEADLENS_TESTING"] = "1"

if os.environ.pop("DATABASE_URL", None) is not None:
    raise RuntimeError(
        "DATABASE_URL was already set in the environment before tests/_bootstrap "
        "ran — refusing to proceed. Tests must never risk touching a real "
        "database. Unset DATABASE_URL before running tests (it should only "
        "come from this project's .env, loaded by the app's own code, not "
        "from your shell or CI environment)."
    )

_real_load_dotenv = dotenv.load_dotenv


def _guarded_load_dotenv(*args, **kwargs):
    """Let .env load normally (other vars still work), but a test process
    must never end up with a real DATABASE_URL, however many times this
    gets called or from wherever — always strip it back out."""
    result = _real_load_dotenv(*args, **kwargs)
    os.environ.pop("DATABASE_URL", None)
    return result


dotenv.load_dotenv = _guarded_load_dotenv


# --- Phase 2 safety net: isolate jarvis_memory.py's storage -----------------
_TEST_STORAGE_DIR = tempfile.mkdtemp(prefix="leadlens_test_storage_")
atexit.register(shutil.rmtree, _TEST_STORAGE_DIR, True)

os.environ.setdefault(
    "LEADLENS_LEARNING_MEMORY_PATH",
    os.path.join(_TEST_STORAGE_DIR, "learning_memory.json"),
)
os.environ.setdefault(
    "LEADLENS_V2_DATABASE_URL",
    "sqlite:///" + os.path.join(_TEST_STORAGE_DIR, "v2_test.db").replace("\\", "/"),
)

try:
    from core.db.base import Base as _V2Base
    import core.db.models as _v2_models  # noqa: F401  (populates _V2Base.metadata)
    from core.db.session import make_engine as _make_v2_engine

    _v2_test_engine = _make_v2_engine()
    _V2Base.metadata.create_all(_v2_test_engine)
    _v2_test_engine.dispose()
except Exception as _v2_setup_error:  # pragma: no cover - defensive only
    # This is a safety net, not a hard requirement — if it fails for some
    # reason (e.g. SQLAlchemy not installed in a minimal environment),
    # don't take down every single test file's import over it. Tests
    # that actually need the V2 schema will fail on their own with a
    # clear error; this print just makes the root cause visible.
    print(f"[tests/_bootstrap] Phase 2 V2 schema safety-net setup failed: {_v2_setup_error}")
