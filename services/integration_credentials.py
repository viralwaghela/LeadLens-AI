"""Phase 6 — per-organization integration credential administration and
resolution.

Replaces the assumption "all clinics share one deployment-global set of
WhatsApp/Gmail/Calendar credentials" with "each organization has its own
encrypted, organization-scoped integration configuration" — while
keeping the existing integration adapters (integrations/*.py) and their
working API-calling logic completely intact. This module is the only
place that reads/writes core/db/models/integration.py's
OrganizationIntegration table; everything else (adapters, call sites)
goes through resolve_provider_credentials() below.

Design (see docs/V2_PHASE6_INTEGRATION_CREDENTIALS.md for the full
writeup):

    configure_integration() / disable_integration()
        Admin operations. Always scoped to tenant_context.organization_id
        — never accept a caller-supplied organization_id. Secret fields
        are Fernet-encrypted (services/credential_encryption.py) before
        being written; non-secret configuration fields are stored as
        plain JSON.

    resolve_credentials() / resolve_provider_credentials()
        The read path every adapter call site uses. Looks up exactly one
        row: (tenant_context.organization_id, provider). If that row is
        ACTIVE, decrypts and returns its credentials. If the row is
        DISABLED or in ERROR, returns None — a disabled/broken
        integration NEVER silently falls back to the legacy environment
        credentials (Phase 6 spec section 12). Only a genuinely ABSENT
        or UNCONFIGURED row is eligible for the transitional environment
        fallback below.

    Transitional environment fallback (Phase 6 spec sections 7-8)
        Existing deployments depend on WHATSAPP_ACCESS_TOKEN /
        GOOGLE_SERVICE_ACCOUNT_FILE / etc. env vars today. Falling back
        to those is allowed ONLY when:
          1. LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED is set, AND
          2. tenant_context.organization_id is EXACTLY the transitional/
             default organization (resolve_transitional_organization_id()
             — the same trusted, non-injectable resolver every prior
             phase uses).
        Any other organization with an absent tenant credential gets
        None (fail closed) — never the deployment's env credentials.
        This is the section-8 adversarial-safety rule and is covered by
        tests/test_phase6_integration_credentials.py's cross-tenant
        fallback tests.

Nothing here makes a live external API call — this module only resolves
configuration/credentials. Connectivity checks live in
scripts/verify_integration_credentials.py (spec section 14), invoked
manually, never from CI or automatically.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.db.models.integration import (
    IntegrationProvider,
    IntegrationStatus,
    OrganizationIntegration,
)
from core.db.session import make_engine, session_scope
from core.identity.tenant_context import TenantContext, resolve_transitional_organization_id
from services.credential_encryption import (
    CredentialDecryptionError,
    CredentialEncryptionError,
    decrypt_credential_fields,
    encrypt_credential_fields,
)

_LOG = logging.getLogger(__name__)

ENV_FALLBACK_ENABLED = os.getenv("LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}

# Which fields are secret (encrypted) vs. plain configuration, per
# provider — matches integrations/*.py's actual env-var reads exactly
# (see docs/V2_PHASE6_INTEGRATION_CREDENTIALS.md section 0's audit
# table). Gmail and Calendar both use a Google service-account
# credential; either the raw JSON key content (preferred for real
# per-organization secrets) or a file path (matching the legacy
# single-file env behavior) may be supplied under
# "service_account_json" / "service_account_file" — the adapter/factory
# layer accepts whichever is present.
SECRET_FIELDS: dict[IntegrationProvider, tuple[str, ...]] = {
    IntegrationProvider.WHATSAPP: ("access_token",),
    IntegrationProvider.GMAIL: ("service_account_json", "service_account_file"),
    IntegrationProvider.GOOGLE_CALENDAR: ("service_account_json", "service_account_file"),
}
CONFIG_FIELDS: dict[IntegrationProvider, tuple[str, ...]] = {
    IntegrationProvider.WHATSAPP: ("phone_number_id", "api_version"),
    IntegrationProvider.GMAIL: ("delegated_user",),
    IntegrationProvider.GOOGLE_CALENDAR: ("calendar_id",),
}
REQUIRED_FIELDS: dict[IntegrationProvider, tuple[str, ...]] = {
    IntegrationProvider.WHATSAPP: ("access_token", "phone_number_id"),
    IntegrationProvider.GMAIL: ("delegated_user",),  # + one of the two secret fields, checked separately
    IntegrationProvider.GOOGLE_CALENDAR: (),  # calendar_id defaults to "primary"; only the secret is required
}

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = make_engine()
    return _ENGINE


@dataclass(frozen=True)
class ResolvedCredential:
    """What resolve_credentials() returns on success. `source` is
    "tenant" (a real per-organization row) or "env_fallback" (the
    transitional single-clinic bridge) — factories/call sites may use
    this for logging/status display, never for authorization decisions."""

    provider: IntegrationProvider
    organization_id: int
    secret: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    source: str = "tenant"


# ---------------------------------------------------------------------------
# Structural validation (no live API calls — spec section 13)
# ---------------------------------------------------------------------------

def validate_fields(provider: IntegrationProvider, secret_fields: dict[str, Any], configuration_fields: dict[str, Any]) -> list[str]:
    """Returns a list of problems (empty = valid). Structural only:
    required keys present and non-empty. Never attempts a live
    connectivity check — see scripts/verify_integration_credentials.py
    for that, invoked manually."""
    problems: list[str] = []
    for key in REQUIRED_FIELDS.get(provider, ()):
        value = configuration_fields.get(key) if key in CONFIG_FIELDS.get(provider, ()) else secret_fields.get(key)
        if not str(value or "").strip():
            problems.append(f"missing required field: {key}")
    if provider in (IntegrationProvider.GMAIL, IntegrationProvider.GOOGLE_CALENDAR):
        if not (str(secret_fields.get("service_account_json") or "").strip() or str(secret_fields.get("service_account_file") or "").strip()):
            problems.append("missing required field: service_account_json or service_account_file")
    return problems


# ---------------------------------------------------------------------------
# Admin operations — always scoped to tenant_context.organization_id
# ---------------------------------------------------------------------------

def get_integration(session: Session, organization_id: int, provider: IntegrationProvider) -> OrganizationIntegration | None:
    return (
        session.query(OrganizationIntegration)
        .filter(
            OrganizationIntegration.organization_id == organization_id,
            OrganizationIntegration.provider == provider,
        )
        .one_or_none()
    )


def configure_integration(
    session: Session,
    tenant_context: TenantContext,
    provider: IntegrationProvider,
    *,
    secret_fields: dict[str, Any] | None = None,
    configuration_fields: dict[str, Any] | None = None,
    actor: str = "system",
) -> OrganizationIntegration:
    """Create or update this organization's integration row. Encrypts
    `secret_fields` as one blob (if provided — omitting it leaves any
    previously-stored secret untouched, so a caller can update just
    configuration without re-supplying the secret). Transactional: the
    encrypt + upsert + flush happen as one unit: either the whole update
    lands or none of it does (spec section 27)."""
    row = get_integration(session, tenant_context.organization_id, provider)
    if row is None:
        row = OrganizationIntegration(
            organization_id=tenant_context.organization_id,
            provider=provider,
            status=IntegrationStatus.UNCONFIGURED,
        )
        session.add(row)

    if secret_fields is not None:
        ciphertext, key_version = encrypt_credential_fields(secret_fields)
        row.encrypted_credentials = ciphertext
        row.encryption_key_version = key_version

    if configuration_fields is not None:
        merged = deserialize_configuration(row.configuration)
        merged.update(configuration_fields)
        row.configuration = json.dumps(merged, ensure_ascii=False, default=str)

    row.status = IntegrationStatus.ACTIVE
    row.last_error_at = None
    row.last_error_code = None
    session.flush()

    _audit(actor, "integration_configured", provider, tenant_context.organization_id)
    return row


def disable_integration(
    session: Session, tenant_context: TenantContext, provider: IntegrationProvider, *, actor: str = "system",
) -> OrganizationIntegration | None:
    row = get_integration(session, tenant_context.organization_id, provider)
    if row is None:
        return None
    row.status = IntegrationStatus.DISABLED
    session.flush()
    _audit(actor, "integration_disabled", provider, tenant_context.organization_id)
    return row


def safe_metadata(row: OrganizationIntegration) -> dict[str, Any]:
    """Everything about a row EXCEPT its encrypted secret — safe to
    return from an admin API/response. `configuration` is included since
    it is, by construction, never secret (see CONFIG_FIELDS)."""
    return {
        "provider": row.provider.value,
        "status": row.status.value,
        "configuration": deserialize_configuration(row.configuration),
        "encryption_key_version": row.encryption_key_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
        "last_error_code": row.last_error_code,
    }


def deserialize_configuration(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _audit(actor: str, action: str, provider: IntegrationProvider, organization_id: int, *, error_category: str = "") -> None:
    """Routes through the existing, already-audited security_service.audit_event()
    (which itself shadow-syncs into the tenant-scoped SecurityAuditEvent
    table via services/tenant_operational_sync.py — see
    docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md). Detail is deliberately
    limited to provider name + optional error category — never secret
    values, never full configuration."""
    try:
        from services.security_service import audit_event

        detail = f"provider={provider.value}"
        if error_category:
            detail += f" error_category={error_category}"
        audit_event(actor, action, "integration", detail)
    except Exception:  # noqa: BLE001 - audit must never break the caller
        _LOG.error("Phase 6 audit_event failed for %s/%s.", action, provider.value, exc_info=True)


# ---------------------------------------------------------------------------
# Resolution — the read path every adapter call site uses
# ---------------------------------------------------------------------------

def legacy_env_credentials(provider: IntegrationProvider) -> dict[str, Any] | None:
    """Reconstructs exactly what integrations/*.py's own __init__ already
    reads from the environment today — reused here (not duplicated
    logic-wise beyond field names) so the transitional fallback produces
    byte-identical credentials to pre-Phase-6 behavior. Returns None if
    the legacy env vars aren't set (mirrors each adapter's own
    `configured` check)."""
    if provider == IntegrationProvider.WHATSAPP:
        token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        if not (token and phone_number_id):
            return None
        return {
            "secret": {"access_token": token},
            "configuration": {
                "phone_number_id": phone_number_id,
                "api_version": os.getenv("WHATSAPP_API_VERSION", "v23.0").strip() or "v23.0",
            },
        }
    if provider == IntegrationProvider.GMAIL:
        path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        delegated_user = os.getenv("GMAIL_DELEGATED_USER", "").strip()
        if not (path and delegated_user):
            return None
        return {
            "secret": {"service_account_file": path},
            "configuration": {"delegated_user": delegated_user},
        }
    if provider == IntegrationProvider.GOOGLE_CALENDAR:
        path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        if not path:
            return None
        return {
            "secret": {"service_account_file": path},
            "configuration": {"calendar_id": os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"},
        }
    return None


def resolve_credentials(
    session: Session, tenant_context: TenantContext, provider: IntegrationProvider,
) -> ResolvedCredential | None:
    """The core resolution rule (spec sections 5-8). Never accepts an
    organization_id parameter — only tenant_context.organization_id is
    ever consulted. Returns None (never raises for "not configured" —
    callers/adapters already have a safe dry-run fallback for that) on:
      - no row for this org+provider AND fallback not applicable
      - row exists but status is DISABLED or ERROR (never falls back)
      - row exists, ACTIVE, but decryption fails (logged + audited,
        never falls back to another organization's data or to env)
    """
    row = get_integration(session, tenant_context.organization_id, provider)

    if row is not None and row.status in (IntegrationStatus.DISABLED, IntegrationStatus.ERROR):
        return None  # explicit admin state — never falls back (section 12)

    if row is not None and row.status == IntegrationStatus.ACTIVE:
        try:
            secret = decrypt_credential_fields(row.encrypted_credentials or "", row.encryption_key_version or 1)
        except CredentialEncryptionError as exc:
            category = "decryption_failed" if isinstance(exc, CredentialDecryptionError) else "key_unavailable"
            row.status = IntegrationStatus.ERROR
            row.last_error_at = datetime.now(timezone.utc)
            row.last_error_code = category
            try:
                session.flush()
            except Exception:  # noqa: BLE001 - marking the error state must never itself crash resolution
                pass
            _audit("system", "credential_resolution_failed", provider, tenant_context.organization_id, error_category=category)
            return None
        return ResolvedCredential(
            provider=provider,
            organization_id=tenant_context.organization_id,
            secret=secret,
            configuration=deserialize_configuration(row.configuration),
            source="tenant",
        )

    # Absent or UNCONFIGURED — the only case where the transitional
    # environment fallback is even considered.
    if not ENV_FALLBACK_ENABLED:
        return None
    if tenant_context.organization_id != resolve_transitional_organization_id(session):
        # Section 8's hard rule: fallback is allowed ONLY for the one
        # transitional/default organization, never for any other org
        # with a merely-absent tenant credential.
        return None
    fallback = legacy_env_credentials(provider)
    if fallback is None:
        return None
    return ResolvedCredential(
        provider=provider,
        organization_id=tenant_context.organization_id,
        secret=fallback["secret"],
        configuration=fallback["configuration"],
        source="env_fallback",
    )


def resolve_provider_credentials(tenant_context: TenantContext, provider: IntegrationProvider) -> ResolvedCredential | None:
    """Self-contained convenience wrapper (own engine/session) for call
    sites that don't already have a Session open — mirrors
    services/tenant_operational_sync.py's _resolve_context() pattern.
    Never raises: any DB-layer failure is treated as "could not resolve"
    (None), so a database outage degrades to the adapter's own existing
    dry-run behavior rather than crashing a booking confirmation or a
    queued send."""
    try:
        engine = _get_engine()
        with session_scope(engine) as session:
            return resolve_credentials(session, tenant_context, provider)
    except Exception:  # noqa: BLE001 - resolution failure must never crash the caller
        _LOG.error("Phase 6 credential resolution failed for %s.", provider.value, exc_info=True)
        return None
