"""Phase 6 — tenant-aware integration adapter factories.

The only place that turns a TenantContext into a live
WhatsAppBusinessService / GmailService / GoogleCalendarService instance.
Every live call site (services/integration_manager_v21.py,
services/appointment_messaging.py) should construct adapters through
these functions rather than calling the adapter classes directly, so
"which organization's credentials" is always answered by
services/integration_credentials.resolve_provider_credentials() — never
by an adapter reading os.environ on its own initiative for a
multi-tenant deployment.

If credential resolution returns nothing (not configured, resolution
machinery unavailable, disabled, etc.) the adapter is constructed with
no `credentials` override — which reproduces its exact pre-Phase-6
behavior (read the deployment env vars directly, dry-run if unset).
This is deliberate: a resolution hiccup must degrade to today's
existing single-clinic behavor, not crash a booking confirmation.
"""
from __future__ import annotations

from core.db.models.integration import IntegrationProvider
from core.identity.tenant_context import TenantContext
from integrations.calendar_service import GoogleCalendarService
from integrations.gmail_service import GmailService
from integrations.whatsapp_service import WhatsAppBusinessService
from services.integration_credentials import resolve_provider_credentials


def _resolved_credentials(tenant_context: TenantContext, provider: IntegrationProvider) -> dict | None:
    resolved = resolve_provider_credentials(tenant_context, provider)
    if resolved is None:
        return None
    return {**resolved.secret, **resolved.configuration}


def get_whatsapp_client(tenant_context: TenantContext, *, dry_run: bool | None = None) -> WhatsAppBusinessService:
    return WhatsAppBusinessService(
        dry_run=dry_run, credentials=_resolved_credentials(tenant_context, IntegrationProvider.WHATSAPP),
    )


def get_gmail_client(tenant_context: TenantContext, *, dry_run: bool | None = None) -> GmailService:
    return GmailService(
        dry_run=dry_run, credentials=_resolved_credentials(tenant_context, IntegrationProvider.GMAIL),
    )


def get_calendar_client(tenant_context: TenantContext, *, dry_run: bool | None = None) -> GoogleCalendarService:
    return GoogleCalendarService(
        dry_run=dry_run, credentials=_resolved_credentials(tenant_context, IntegrationProvider.GOOGLE_CALENDAR),
    )
