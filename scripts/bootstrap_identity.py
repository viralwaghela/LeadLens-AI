"""Explicit, manual, idempotent bootstrap for the future identity cutover.

NOT run automatically by anything. Not imported by app.py, dashboard.py,
core/auth.py, or any live code path — the only way this ever executes is
a human typing `python scripts/bootstrap_identity.py` at a terminal.

What it does, using whatever DATABASE_URL the environment currently has
(same resolution as core/memory.py and core/db/session.py):
    1. get-or-create an Organization by slug (reuses
       core.identity.organization_service — same behavior as Phase 0's
       scripts/bootstrap_beyond_pain_org.py, not duplicated logic)
    2. get-or-create a User by email
    3. get-or-create an OWNER Membership linking the two

Idempotency: safe to run repeatedly. Running this five times produces
exactly one Organization, one User, and one Membership — never five of
each, and never an error on the second-and-later runs.

Critical safety property: this NEVER overwrites an existing user's
password. If a user with the given email already exists, their
password_hash is left untouched — --owner-password is ignored for an
existing user, and the script says so explicitly.

It does NOT modify core/auth.py, does NOT touch core/memory.py's
memory_store table, and does NOT disable or otherwise affect the live
shared-password login path in any way. This is preparation for a future,
separate, explicitly-discussed cutover phase only.

Usage (from the repo root, with the venv active):

    python scripts/bootstrap_identity.py --dry-run
    python scripts/bootstrap_identity.py \\
        --org-slug beyond-pain --org-name "Beyond Pain Physiotherapy and Pilates" \\
        --owner-email owner@beyondpain.example --owner-password "..."

If --owner-password is omitted, the script prompts for it via getpass
(never accepts it as a bare positional argument, to keep it out of shell
history by default — though --owner-password is still provided for
scripted/CI use where that tradeoff is acceptable).

Before running this against a real deployment's DATABASE_URL: know which
database that is first, the same way you would for
scripts/bootstrap_beyond_pain_org.py. See docs/V2_PHASE1_IDENTITY.md.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.db.models.identity import MembershipRole, MembershipStatus, UserStatus  # noqa: E402
from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402
from core.identity import membership_service, organization_service, user_service  # noqa: E402

DEFAULT_ORG_SLUG = "beyond-pain"
DEFAULT_ORG_NAME = "Beyond Pain Physiotherapy and Pilates"


def bootstrap(
    *,
    org_slug: str = DEFAULT_ORG_SLUG,
    org_name: str = DEFAULT_ORG_NAME,
    owner_email: str,
    owner_password: str | None,
    dry_run: bool = False,
) -> None:
    database_url = get_database_url()
    print(f"Target database: {database_url}")

    engine = make_engine(database_url)
    try:
        with session_scope(engine) as session:
            org = organization_service.get_organization_by_slug(session, org_slug)
            if org is not None:
                print(f"Organization already exists: id={org.id}, slug={org.slug!r}. No change made.")
            elif dry_run:
                print(f"[dry-run] Would create Organization(slug={org_slug!r}, name={org_name!r}).")
            else:
                org = organization_service.create_organization(session, name=org_name, slug=org_slug)
                print(f"Created Organization(id={org.id}, slug={org.slug!r}).")

            existing_user = user_service.get_user_by_email(session, owner_email)
            if existing_user is not None:
                print(
                    f"User already exists: id={existing_user.id}, email={existing_user.email!r}. "
                    "Password left untouched (this script never overwrites an existing password)."
                )
                owner = existing_user
            elif dry_run:
                print(f"[dry-run] Would create User(email={owner_email!r}).")
                owner = None
            else:
                if not owner_password:
                    raise SystemExit(
                        "no existing user found for --owner-email and no --owner-password "
                        "was supplied — cannot create a new user without a password"
                    )
                owner = user_service.create_user(
                    session, email=owner_email, password=owner_password, status=UserStatus.ACTIVE
                )
                print(f"Created User(id={owner.id}, email={owner.email!r}).")

            if dry_run:
                print("[dry-run] Would ensure an ACTIVE OWNER membership between them. Nothing written.")
                return

            if org is None or owner is None:
                # Shouldn't happen outside dry-run, but guards against a
                # future refactor silently skipping a required step.
                raise RuntimeError("organization and owner must both exist before membership creation")

            existing_membership = membership_service.get_membership_for_user_org(
                session, user_id=owner.id, organization_id=org.id
            )
            if existing_membership is not None:
                print(
                    f"Membership already exists: id={existing_membership.id}, "
                    f"role={existing_membership.role.value}, status={existing_membership.status.value}. "
                    "No change made."
                )
                return

            membership = membership_service.create_membership(
                session,
                user_id=owner.id,
                organization_id=org.id,
                role=MembershipRole.OWNER,
                status=MembershipStatus.ACTIVE,
            )
            print(f"Created Membership(id={membership.id}, role=OWNER, status=ACTIVE).")
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-slug", default=DEFAULT_ORG_SLUG)
    parser.add_argument("--org-name", default=DEFAULT_ORG_NAME)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument(
        "--owner-password",
        default=None,
        help="Omit to be prompted via getpass (not echoed, not in shell history).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would happen; write nothing."
    )
    args = parser.parse_args()

    owner_password = args.owner_password
    if not args.dry_run and owner_password is None:
        # Only prompt if we might actually need it (a brand-new user) —
        # bootstrap() itself decides whether an existing user makes the
        # password moot, but prompting up front keeps the CLI simple.
        owner_password = getpass.getpass(f"Password for new user {args.owner_email!r} (if new): ")

    bootstrap(
        org_slug=args.org_slug,
        org_name=args.org_name,
        owner_email=args.owner_email,
        owner_password=owner_password,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
