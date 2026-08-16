"""Phase 6 — per-organization integration credentials.

One provider-neutral table rather than one table per provider: every
provider (Gmail, Google Calendar, WhatsApp) reduces to the same shape —
some secret fields (access tokens, service-account keys) and some
non-secret configuration fields (phone number ID, delegated sender,
calendar ID) — so a single table with an `encrypted_credentials` blob
plus a `configuration` JSON blob covers all of them without inventing
per-provider columns. See services/integration_credentials.py for the
encrypt/decrypt/resolve logic that reads and writes this table, and
docs/V2_PHASE6_INTEGRATION_CREDENTIALS.md for the full design.
"""
from __future__ import annotations

import enum

from sqlalchemy import DateTime, Enum, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.db.models.mixins import OrgScopedMixin, TimestampMixin


class IntegrationProvider(str, enum.Enum):
    GMAIL = "GMAIL"
    GOOGLE_CALENDAR = "GOOGLE_CALENDAR"
    WHATSAPP = "WHATSAPP"


class IntegrationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"
    UNCONFIGURED = "UNCONFIGURED"


class OrganizationIntegration(OrgScopedMixin, TimestampMixin, Base):
    """One row per (organization, provider). `encrypted_credentials` holds
    only the genuinely secret fields (e.g. WhatsApp access token, Gmail/
    Calendar service-account key JSON), Fernet-encrypted as one JSON blob
    — never plaintext, never logged. `configuration` holds the
    non-secret fields (phone number ID, delegated sender email, calendar
    ID) as plain JSON, since those are safe to read for status displays
    and don't need decryption to inspect. `encryption_key_version` records
    which master key encrypted this row's secret payload, so a future key
    rotation can decrypt old rows with their original key before
    re-encrypting under a new one — see
    services/integration_credentials.py's ENCRYPTION_FORMAT_VERSION."""

    __tablename__ = "organization_integrations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "provider", name="uq_org_integration_org_provider"
        ),
        Index("ix_org_integration_org_provider", "organization_id", "provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(IntegrationProvider, name="integration_provider"), nullable=False
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, name="integration_status"),
        nullable=False,
        default=IntegrationStatus.UNCONFIGURED,
    )
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    encryption_format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    encryption_key_version: Mapped[int | None] = mapped_column(Integer)
    configuration: Mapped[str | None] = mapped_column(Text)  # JSON-encoded, non-secret
    last_verified_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
