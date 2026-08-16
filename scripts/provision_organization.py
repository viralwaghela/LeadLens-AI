"""Phase 8 — explicit, operator-only organization provisioning.

NOT run automatically by anything, never imported by app.py/dashboard.py,
and not a public self-signup flow — this is deliberately a trusted,
platform-operator-level CLI, kept separate from the Phase 1 organization
RBAC model (an OWNER of Clinic A cannot run this to create Clinic B; the
only way this ever executes is a human with shell access to the
deployment typing the command below).

What it does, idempotently:

    1. get-or-create an Organization by slug (never a duplicate — see
       core.identity.organization_service.create_organization's own
       existing-slug check).
    2. get-or-create the initial OWNER User (--owner-email), by email —
       an existing user's password is NEVER touched or overwritten; a
       brand-new user is created with the password you supply.
    3. get-or-create an ACTIVE OWNER Membership linking that user to
       that organization (safe to re-run: a second run against the same
       slug/email either confirms the existing membership or, for a
       genuinely different email, adds a second OWNER — never a
       duplicate membership for the same (user, organization) pair).

No secret (password) is ever printed or logged. Automations/scheduler
default OFF for a newly-provisioned organization
(OrganizationSettings.automations_enabled) — enable that separately,
deliberately, once the clinic's configuration has actually been
reviewed.

Usage (from the repo root, with the venv active):

    python scripts/provision_organization.py \\
        --organization-name "Riverside Physiotherapy" \\
        --slug riverside-physio \\
        --owner-email owner@riverside.example
        # prompts for the owner's password (hidden input) unless
        # --owner-password-env is given

    python scripts/provision_organization.py --dry-run \\
        --organization-name "Riverside Physiotherapy" --slug riverside-physio \\
        --owner-email owner@riverside.example

    # Attach an EXISTING user (identified by email) as OWNER of a new
    # organization instead of creating a new one — no password needed:
    python scripts/provision_organization.py \\
        --organization-name "Second Clinic" --slug second-clinic \\
        --owner-email existing-owner@example.com --existing-user

Before running this against a real deployment's DATABASE_URL: know which
database DATABASE_URL currently points at — this writes real rows there.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.db.models.identity import MembershipRole  # noqa: E402
from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402
from core.identity import (  # noqa: E402
    membership_service,
    organization_service,
    user_service,
)
from core.identity.membership_service import DuplicateMembershipError  # noqa: E402
from core.identity.organization_service import DuplicateOrganizationError  # noqa: E402
from core.identity.user_service import DuplicateUserError  # noqa: E402


class ProvisioningResult:
    def __init__(self) -> None:
        self.organization_id: int | None = None
        self.organization_created = False
        self.user_id: int | None = None
        self.user_created = False
        self.membership_id: int | None = None
        self.membership_created = False


def provision(
    *,
    organization_name: str,
    slug: str,
    owner_email: str,
    owner_password: str | None,
    existing_user: bool,
    dry_run: bool = False,
) -> ProvisioningResult:
    result = ProvisioningResult()
    database_url = get_database_url()
    print(f"Target database: {database_url}")

    if dry_run:
        print(
            f"[dry-run] Would provision organization slug={slug!r} name={organization_name!r}, "
            f"owner={owner_email!r} ({'existing user' if existing_user else 'new user'}). "
            "Nothing written."
        )
        return result

    engine = make_engine(database_url)
    try:
        with session_scope(engine) as session:
            try:
                org = organization_service.create_organization(session, name=organization_name, slug=slug)
                result.organization_created = True
                print(f"Created organization id={org.id} slug={org.slug!r}.")
            except DuplicateOrganizationError:
                org = organization_service.get_organization_by_slug(session, slug)
                print(f"Organization slug={slug!r} already exists (id={org.id}) — using it, no change made.")
            result.organization_id = org.id

            if existing_user:
                user = user_service.get_user_by_email(session, owner_email)
                if user is None:
                    raise SystemExit(
                        f"--existing-user was given but no user with email {owner_email!r} exists. "
                        "Omit --existing-user to create a new one instead."
                    )
                print(f"Using existing user id={user.id} ({user.email}) — password NOT touched.")
            else:
                try:
                    if not owner_password:
                        raise SystemExit("--owner-email refers to a new user, but no password was supplied.")
                    user = user_service.create_user(session, email=owner_email, password=owner_password)
                    result.user_created = True
                    print(f"Created user id={user.id} ({user.email}).")
                except DuplicateUserError:
                    user = user_service.get_user_by_email(session, owner_email)
                    print(
                        f"A user with email {owner_email!r} already exists (id={user.id}) — "
                        "using it, password NOT touched. Pass --existing-user next time to skip "
                        "the password prompt entirely."
                    )
            result.user_id = user.id

            try:
                membership = membership_service.create_membership(
                    session, user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER,
                )
                result.membership_created = True
                print(f"Created OWNER membership id={membership.id}.")
            except DuplicateMembershipError:
                membership = membership_service.get_membership_for_user_org(
                    session, user_id=user.id, organization_id=org.id,
                )
                print(f"Membership already exists (id={membership.id}, role={membership.role.value}) — no change made.")
            result.membership_id = membership.id
    finally:
        engine.dispose()

    print(
        f"Provisioning complete: organization_id={result.organization_id}, "
        f"user_id={result.user_id}, membership_id={result.membership_id}. "
        "Automations remain disabled for this organization until explicitly enabled "
        "(see scripts/verify_multi_org_readiness.py to check status)."
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--organization-name", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument(
        "--existing-user", action="store_true",
        help="Attach an already-existing user (by email) as OWNER instead of creating a new one. "
        "No password needed or accepted.",
    )
    parser.add_argument(
        "--owner-password-env", default=None,
        help="Name of an environment variable holding the new owner's password "
        "(avoids an interactive prompt, e.g. for scripted/CI use). Ignored with --existing-user.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen; write nothing.")
    args = parser.parse_args()

    owner_password = None
    if not args.existing_user and not args.dry_run:
        if args.owner_password_env:
            owner_password = os.environ.get(args.owner_password_env, "")
            if not owner_password:
                raise SystemExit(f"Environment variable {args.owner_password_env!r} is not set or empty.")
        else:
            owner_password = getpass.getpass(f"Password for new owner {args.owner_email}: ")

    provision(
        organization_name=args.organization_name,
        slug=args.slug,
        owner_email=args.owner_email,
        owner_password=owner_password,
        existing_user=args.existing_user,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
