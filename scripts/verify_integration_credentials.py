"""Phase 6 — safe, read-only status verifier for organization integration
credentials.

Reports, per organization/provider: configured?, active?, does the
stored ciphertext actually decrypt with the current master key?, are
the provider's required fields present?, would legacy environment
fallback currently apply? NEVER prints a secret value — only booleans,
counts, and safe metadata (spec section 22).

This is configuration verification, not connectivity verification: it
never makes a live WhatsApp/Gmail/Calendar API call (that would risk
sending a real message/email or hitting a rate limit just to check
status — spec section 14 keeps those separate and explicitly manual).

Usage (from the repo root, with the venv active):

    python scripts/verify_integration_credentials.py
    python scripts/verify_integration_credentials.py --organization my-clinic-slug
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

from core.db.models.integration import IntegrationProvider, IntegrationStatus  # noqa: E402
from core.db.session import make_engine, session_scope  # noqa: E402
from core.identity.default_organization import DEFAULT_ORGANIZATION_SLUG  # noqa: E402
from core.identity.organization_service import get_organization_by_slug  # noqa: E402
from core.identity.tenant_context import resolve_transitional_organization_id  # noqa: E402
from services.credential_encryption import CredentialEncryptionError, decrypt_credential_fields  # noqa: E402
from services.integration_credentials import ENV_FALLBACK_ENABLED, get_integration, legacy_env_credentials  # noqa: E402


def verify_one(session, org, provider: IntegrationProvider) -> dict[str, object]:
    row = get_integration(session, org.id, provider)
    report: dict[str, object] = {
        "organization": org.slug,
        "provider": provider.value,
        "configured": row is not None and row.encrypted_credentials is not None,
        "active": row is not None and row.status == IntegrationStatus.ACTIVE,
        "status": row.status.value if row is not None else "NO_ROW",
        "credential_decryptable": None,
        "required_fields_present": None,
        "legacy_fallback_would_apply": False,
    }

    if row is not None and row.encrypted_credentials:
        try:
            secret = decrypt_credential_fields(row.encrypted_credentials, row.encryption_key_version or 1)
            report["credential_decryptable"] = True
            from services.integration_credentials import validate_fields, deserialize_configuration

            problems = validate_fields(provider, secret, deserialize_configuration(row.configuration))
            report["required_fields_present"] = not problems
        except CredentialEncryptionError:
            report["credential_decryptable"] = False
            report["required_fields_present"] = False

    if report["configured"] is False or row is None or row.status == IntegrationStatus.UNCONFIGURED:
        transitional_id = resolve_transitional_organization_id(session)
        if ENV_FALLBACK_ENABLED and org.id == transitional_id:
            report["legacy_fallback_would_apply"] = legacy_env_credentials(provider) is not None

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--organization", default=DEFAULT_ORGANIZATION_SLUG)
    args = parser.parse_args()

    engine = make_engine()
    with session_scope(engine) as session:
        org = get_organization_by_slug(session, args.organization)
        if org is None:
            print(f"No organization with slug {args.organization!r} exists.")
            return
        print(f"Organization: {org.slug} (id={org.id}, status={org.status.value})")
        print(f"LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED: {ENV_FALLBACK_ENABLED}")
        print("-" * 72)
        header = f"{'Provider':<16}{'Configured':<12}{'Active':<8}{'Decryptable':<13}{'Fields OK':<11}{'Fallback?':<10}"
        print(header)
        for provider in IntegrationProvider:
            report = verify_one(session, org, provider)
            print(
                f"{report['provider']:<16}"
                f"{str(report['configured']):<12}"
                f"{str(report['active']):<8}"
                f"{str(report['credential_decryptable']):<13}"
                f"{str(report['required_fields_present']):<11}"
                f"{str(report['legacy_fallback_would_apply']):<10}"
            )


if __name__ == "__main__":
    main()
