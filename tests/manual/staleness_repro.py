"""Manual reproduction script for the Supabase read-staleness bug found
on 2026-08-02 while building Phase 1's low_booking_alert check.

What it reproduces: core.memory.get_memory_section() serving a stale
read from a fresh process — stuck for minutes at a time in the worst
observed case, not just a brief lag — while a raw psycopg2 query against
the exact same DATABASE_URL returns fresh, correct data at the same
moment. It does this by writing a batch of uniquely-tagged entries in
one process, then immediately checking both core.memory and a raw
connection from a separate process, repeated over several rounds.

Root cause was never fully confirmed — there's no visibility into
Supabase's Supavisor pooler internals from the client side, and vanilla
Postgres MVCC under READ COMMITTED with no read replicas (confirmed via
pg_is_in_recovery()) has no mechanism that should allow this. What *was*
established:
  - Not a read-replica issue: every connection showed pg_is_in_recovery()
    = false and identical inet_server_addr().
  - Strongly correlated with core.memory._pg_connect()'s pattern of
    running a no-op DDL statement (CREATE TABLE IF NOT EXISTS / ALTER
    TABLE ADD COLUMN IF NOT EXISTS) immediately before every real query,
    combined with going through Supabase's SHARED POOLER hostname
    (aws-0-<region>.pooler.supabase.com). A raw query with no preceding
    DDL, against that same pooled URL, was never observed stale.
  - Never reproduced against Supabase's DIRECT connection
    (db.<project-ref>.supabase.co) in any test run here. That's why
    DATABASE_URL was switched to the direct connection string — see
    .env.example's DATABASE_URL comment and the commit around
    2026-08-02 for the full writeup.

When to run this: if stale-looking reads ever resurface (an alert that
should have deduplicated fires again, a count in the UI looks behind
reality, etc.), especially after any future change to core.memory's
connection handling or to DATABASE_URL. It targets whatever
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
