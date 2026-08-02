"""Manual verification script written on 2026-08-02 while chasing what
looked like a Supabase read-staleness bug during Phase 1's
low_booking_alert work — kept as a general core.memory-vs-raw-Postgres
consistency check, since the actual investigation is a useful template
even though the original bug turned out not to exist.

The full story: several ad-hoc diagnostic one-liners that day checked
production state via core.memory without first calling load_dotenv().
core.memory doesn't auto-load .env (only real app entry points like
scheduler/run_scheduled_checks.py do), so those checks silently read the
local SQLite fallback — frozen from early testing — instead of Postgres,
while raw psycopg2 scripts run alongside them (which did call
load_dotenv()) correctly showed Postgres's real, current state. The
mismatch was mistaken for the database serving stale reads. It wasn't:
every connection showed pg_is_in_recovery() = false and identical
inet_server_addr() (no read replica involved), and once load_dotenv()
was added consistently, core.memory reads matched raw Postgres reads
exactly, every time, including under a real single-connection burst
test. There was no pooler bug, no DDL-related staleness, and switching
DATABASE_URL to Supabase's direct connection (see .env.example) fixed
nothing because nothing there was broken.

What this script actually does, and remains useful for: writes a batch
of uniquely-tagged entries in one process (via core.memory, with
load_dotenv() correctly in place), then immediately checks both
core.memory and a raw psycopg2 connection from a separate process,
repeated over several rounds — a genuine, mechanical way to confirm the
two stay consistent, useful after any future change to core.memory's
connection handling.

When to run this: after changing core.memory's connection or locking
logic, or if a real discrepancy between the app's view and the database
is ever suspected again — though check for a missing load_dotenv() call
in whatever's doing the observing first. It targets whatever
DATABASE_URL currently resolves to via python-dotenv, same as the app
itself — to test a specific connection string instead of whatever's in
.env, temporarily override it:

    DATABASE_URL="postgresql://...pooler.supabase.com:5432/postgres" \\
        python tests/manual/staleness_repro.py

This writes real data (a throwaway "probe"/"probe_ledger" section) to
whatever database DATABASE_URL points at and cleans up after itself —
safe to run against production, but don't run it while something else
is actively writing, since that's a confound, not a controlled test.

Deliberately NOT part of the automated suite in tests/ — every test
there is hard-blocked from ever reaching a real DATABASE_URL (see
tests/_bootstrap.py). This is the one tool meant to reach the real
database on purpose.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]

WRITE_SNIPPET = """
from dotenv import load_dotenv
load_dotenv()
from core.memory import add_memory_entry, get_memory_section
for i in range(6):
    exists = any(
        e.get("data", {{}}).get("key") == f"probe::round{round_num}-{{i}}"
        for e in get_memory_section("probe_ledger")
    )
    if not exists:
        add_memory_entry("probe", {{"round": {round_num}, "i": i}})
        add_memory_entry("probe_ledger", {{"key": f"probe::round{round_num}-{{i}}"}})
print("write done")
"""

CHECK_SNIPPET = """
import os, json
from dotenv import load_dotenv
load_dotenv()
from core.memory import get_memory_section
core_count = len(get_memory_section("probe"))
import psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
with conn.cursor() as cur:
    cur.execute("SELECT payload FROM memory_store WHERE id = 1")
    (payload,) = cur.fetchone()
raw_count = len(json.loads(payload).get("probe", []))
conn.close()
print(f"core.memory sees {core_count}, raw sees {raw_count}")
"""

CLEAR_SNIPPET = """
from dotenv import load_dotenv
load_dotenv()
from core.memory import update_memory
def mutate(m):
    m["probe"] = []
    m["probe_ledger"] = []
update_memory(mutate)
print("cleared")
"""


def run(snippet: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        return f"ERROR: {result.stderr[-2000:]}"
    return result.stdout.strip()


def main(rounds: int = 8) -> int:
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set (SQLite mode) — this script only makes sense against Postgres.")
        return 1

    print(f"Target: {os.environ['DATABASE_URL'].split('@')[1]}")
    print(run(CLEAR_SNIPPET))

    stale_rounds = 0
    for round_num in range(rounds):
        write_out = run(WRITE_SNIPPET.format(round_num=round_num))
        check_out = run(CHECK_SNIPPET)
        expected = (round_num + 1) * 6
        stale = f"core.memory sees {expected}," not in check_out
        print(f"round {round_num}: {write_out} | {check_out} | expected {expected} | {'STALE' if stale else 'ok'}")
        if stale:
            stale_rounds += 1

    print(run(CLEAR_SNIPPET))
    print(f"\nstale rounds: {stale_rounds}/{rounds}")
    return 1 if stale_rounds else 0


if __name__ == "__main__":
    sys.exit(main())
