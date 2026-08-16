"""Phase 6 — explicit, manual migration of an existing deployment's
environment-based integration credentials into encrypted, organization-
scoped OrganizationIntegration rows.

NOT run automatically by anything — never imported by app.py,
dashboard.py, or any live import path. The only way this executes is a
human typing `python scripts/migrate_integration_credentials.py` at a
terminal (spec section 21: "Do not auto-import environment credentials
on app startup"). Reuses services/integration_credentials.py's own
configure_integration() — the exact same write path an admin API would
eventually use — rather than writing to the model directly.

Idempotent and safe to rerun: if the target organization+provider
already has a configured row (encrypted_credentials already set), the
script SKIPS it by default rather than silently overwriting — an
already-migrated or manually-configured credential is never clobbered.
Pass --force to explicitly overwrite. Never prints a secret value,
under --dry-run or otherwise — only which fields *would be* written.

Usage (from the repo root, with the venv active):

    python scripts/migrate_integration_credentials.py --provider whatsapp --dry-run
    python scripts/migrate_integration_credentials.py --provider whatsapp
    python scripts/migrate_integration_credentials.py --provider all --organization my-clinic-slug
    python scripts/migrate_integration_credentials.py --provider gmail --force
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

from core.db.models.integration import IntegrationProvider  # noqa: E402
from core.db.session import make_engine, session_scope  # noqa: E402
from core.identity.default_organization import (  # noqa: E402
    DEFAULT_ORGANIZATION_NAME,
    DEFAULT_ORGANIZATION_SLUG,
)
from core.identity.organization_service import create_organization, get_organization_by_slug  # noqa: E402
from core.identity.tenant_context import ActorType, build_system_context  # noqa: E402
from services.integration_credentials import (  # noqa: E402
    legacy_env_credentials,
    configure_integration,
    get_integration,
    validate_fields,
)

PROVIDER_CHOICES = {
    "whatsapp": IntegrationProvider.WHATSAPP,
    "gmail": IntegrationProvider.GMAIL,
    "calendar": IntegrationProvider.GOOGLE_CALENDAR,
}


def _resolve_organization(session, slug_or_id: str):
    if slug_or_id.isdigit():
        from core.identity.organization_service import get_organization

        org = get_organization(session, int(slug_or_id))
        if org is None:
            raise SystemExit(f"No organization with id {slug_or_id}.")
        return org
    org = get_organization_by_slug(session, slug_or_id)
    if org is not None:
        return org
    if slug_or_id == DEFAULT_ORGANIZATION_SLUG:
        return create_organization(session, name=DEFAULT_ORGANIZATION_NAME, slug=DEFAULT_ORGANIZATION_SLUG)
    raise SystemExit(
        f"No organization with slug {slug_or_id!r} exists. This script never "
        "creates a new organization except the transitional default — create "
        "it explicitly first via core.identity.organization_service."
    )


def _migrate_one(session, org, provider: IntegrationProvider, *, dry_run: bool, force: bool) -> str:
    fallback = legacy_env_credentials(provider)
    if fallback is None:
        return f"{provider.value}: no legacy environment credentials found — nothing to migrate."

    problems = validate_fields(provider, fallback["secret"], fallback["configuration"])
    if problems:
        return f"{provider.value}: environment credentials incomplete ({'; '.join(problems)}) — skipped."

    existing = get_integration(session, org.id, provider)
    if existing is not None and existing.encrypted_credentials and not force:
        return (
            f"{provider.value}: organization already has a configured credential "
            "(not overwritten — pass --force to replace it)."
        )

    # Never print secret values — only which non-secret configuration
    # keys and which secret field NAMES (not values) would be written.
    secret_field_names = sorted(fallback["secret"].keys())
    config_preview = fallback["configuration"]
    if dry_run:
        return (
            f"{provider.value}: DRY RUN — would write secret fields {secret_field_names} "
            f"and configuration {config_preview}."
        )

    tenant_context = build_system_context(session, organization_id=org.id, actor_type=ActorType.AUTOMATION, source="migrate_integration_credentials")
    configure_integration(
        session, tenant_context, provider,
        secret_fields=fallback["secret"], configuration_fields=fallback["configuration"],
        actor="migrate_integration_credentials",
    )
    return f"{provider.value}: migrated (secret fields {secret_field_names}, configuration {config_preview})."


def migrate(*, organization_slug: str, providers: list[IntegrationProvider], dry_run: bool, force: bool, engine=None) -> list[str]:
    engine = engine or make_engine()
    messages: list[str] = []
    with session_scope(engine) as session:
        org = _resolve_organization(session, organization_slug)
        for provider in providers:
            messages.append(_migrate_one(session, org, provider, dry_run=dry_run, force=force))
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--organization", default=DEFAULT_ORGANIZATION_SLUG, help="Organization slug or numeric id (default: the transitional default organization).")
    parser.add_argument("--provider", choices=[*PROVIDER_CHOICES.keys(), "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be migrated; write nothing.")
    parser.add_argument("--force", action="store_true", help="Overwrite an already-configured tenant credential.")
    args = parser.parse_args()

    providers = list(PROVIDER_CHOICES.values()) if args.provider == "all" else [PROVIDER_CHOICES[args.provider]]

    print(f"Target organization: {args.organization}")
    print(f"Providers: {', '.join(p.value for p in providers)}")
    print(f"Mode: {'DRY RUN (no writes)' if args.dry_run else 'LIVE'}{' + FORCE' if args.force else ''}")
    print("-" * 60)
    for message in migrate(organization_slug=args.organization, providers=providers, dry_run=args.dry_run, force=args.force):
        print(message)


if __name__ == "__main__":
    main()
