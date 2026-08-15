"""Explicit, manual, idempotent bootstrap for Beyond Pain's future Organization row.

NOT run automatically by anything. Not imported by app.py, dashboard.py,
or any live code path — the only way this ever executes is a human
typing `python scripts/bootstrap_beyond_pain_org.py` at a terminal.

What it does: creates (or, on a second run, simply confirms/no-ops on)
exactly one Organization row with slug="beyond-pain" in the V2 relational
schema (core/db/), using whatever DATABASE_URL the environment currently
has — same resolution as core/memory.py and core/db/session.py. It does
NOT read, write, or touch core/memory.py's memory_store table at all,
and it does NOT create a User, Membership, or any clinic data — Phase 0
explicitly defers all of that (see docs/V2_COEXISTENCE.md).

Idempotency: get-or-create by slug. Running this five times produces the
same single row every time, never five rows, and never errors on the
second-and-later runs.

Usage (from the repo root, with the venv active):

    python scripts/bootstrap_beyond_pain_org.py                # uses DATABASE_URL
    python scripts/bootstrap_beyond_pain_org.py --dry-run       # prints what it
                                                                  would do, writes nothing
    python scripts/bootstrap_beyond_pain_org.py --name "Beyond Pain Physiotherapy and Pilates"

Before running this against a real deployment's DATABASE_URL: this is
harmless (one additive row in a brand-new, unused-by-the-app table), but
it is still a deliberate write to whatever database DATABASE_URL points
at — know which one that is before running it. See docs/V2_COEXISTENCE.md
for when a later phase is actually expected to run this for real.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.db.models.organization import Organization, OrganizationStatus  # noqa: E402
from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402

DEFAULT_SLUG = "beyond-pain"
DEFAULT_NAME = "Beyond Pain Physiotherapy and Pilates"


def bootstrap(
    slug: str = DEFAULT_SLUG,
    name: str = DEFAULT_NAME,
    dry_run: bool = False,
) -> Organization | None:
    database_url = get_database_url()
    print(f"Target database: {database_url}")

    engine = make_engine(database_url)
    try:
        with session_scope(engine) as session:
            existing = (
                session.query(Organization)
                .filter(Organization.slug == slug)
                .one_or_none()
            )
            if existing is not None:
                print(
                    f"Already exists: Organization(id={existing.id}, "
                    f"slug={existing.slug!r}, name={existing.name!r}, "
                    f"status={existing.status.value}). No change made."
                )
                return existing

            if dry_run:
                print(
                    f"[dry-run] Would create Organization(slug={slug!r}, "
                    f"name={name!r}, status=ACTIVE). Nothing written."
                )
                return None

            org = Organization(name=name, slug=slug, status=OrganizationStatus.ACTIVE)
            session.add(org)
            session.flush()  # populate org.id without ending the transaction
            print(f"Created: Organization(id={org.id}, slug={org.slug!r}, name={org.name!r}).")
            return org
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would happen; write nothing."
    )
    args = parser.parse_args()
    bootstrap(slug=args.slug, name=args.name, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
