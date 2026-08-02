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

Usage, as the very first line of a test file:

    import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)
"""
import os

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
