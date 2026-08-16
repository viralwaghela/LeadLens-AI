"""Phase 9 — centralized startup configuration validation.

Read-only: inspects environment variables and reports findings. Never
prints a secret's value — only whether a required one is present, and a
generic description of what's missing/unsafe. Not wired into app.py's
startup path automatically (that would be a behavior change to the live
app requiring its own sign-off) — call `validate_configuration()`
explicitly from `scripts/health_check.py` / `scripts/production_readiness.py`,
or from a deployment's own startup script if an operator chooses to.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


@dataclass
class ConfigFinding:
    level: str  # "OK" | "WARN" | "FAIL"
    message: str


@dataclass
class ConfigReport:
    findings: list[ConfigFinding] = field(default_factory=list)

    def add(self, level: str, message: str) -> None:
        self.findings.append(ConfigFinding(level, message))

    @property
    def has_fail(self) -> bool:
        return any(f.level == "FAIL" for f in self.findings)

    @property
    def has_warn(self) -> bool:
        return any(f.level == "WARN" for f in self.findings)


# Every V2 migration flag introduced across Phases 3-8.1, for the
# "unsafe combination" checks below and for scripts/production_readiness.py's
# flag inventory — see docs/V2_PHASE9_PRODUCTION_HARDENING.md's
# feature-flag classification for what each one means and its
# recommended production value.
V2_FLAGS = (
    "LEADLENS_V2_DUAL_WRITE_ENABLED",
    "LEADLENS_V2_READ_PATIENTS",
    "LEADLENS_V2_READ_APPOINTMENTS",
    "LEADLENS_V2_READ_PACKAGES",
    "LEADLENS_V2_READ_PACKAGE_TEMPLATES",
    "LEADLENS_V2_READ_PAYMENTS",
    "LEADLENS_V2_READ_PROGRESS_NOTES",
    "LEADLENS_V2_READ_LEADS",
    "LEADLENS_V2_READ_CORPORATE_CLIENTS",
    "LEADLENS_V2_READ_PRACTITIONERS",
    "LEADLENS_V2_READ_SERVICES",
    "LEADLENS_V2_READ_COMPARE",
    "LEADLENS_V2_READ_FAILSAFE_LEGACY",
    "LEADLENS_V2_TENANT_CONTEXT_ENABLED",
    "LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED",
    "LEADLENS_V2_AUTH_ENABLED",
    "LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED",
    "LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED",
    "LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED",
    "LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED",
    "LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED",
)


def validate_configuration() -> ConfigReport:
    report = ConfigReport()

    # --- Core storage ---------------------------------------------------
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        report.add("OK", "DATABASE_URL is set (Postgres backend).")
    else:
        report.add(
            "WARN",
            "DATABASE_URL is not set — falling back to local SQLite. Fine for local "
            "dev; on an ephemeral-filesystem host (Streamlit Community Cloud, most "
            "free tiers), data will NOT survive a redeploy/restart.",
        )

    # --- Auth / RBAC ------------------------------------------------------
    v2_auth = _flag("LEADLENS_V2_AUTH_ENABLED")
    if v2_auth:
        session_secret = os.getenv("LEADLENS_V2_AUTH_SESSION_SECRET", "").strip()
        if session_secret:
            report.add("OK", "LEADLENS_V2_AUTH_ENABLED is on and LEADLENS_V2_AUTH_SESSION_SECRET is set.")
        else:
            report.add(
                "WARN",
                "LEADLENS_V2_AUTH_ENABLED is on but LEADLENS_V2_AUTH_SESSION_SECRET is not set — "
                "a random key is generated per process, so reload tokens (workspace-switch "
                "continuity) stop working across a process restart. Not a security issue, "
                "just an avoidable rough edge — set this explicitly for production.",
            )
    else:
        app_password = os.getenv("APP_PASSWORD", "").strip()
        if not app_password:
            report.add(
                "FAIL",
                "Neither LEADLENS_V2_AUTH_ENABLED nor APP_PASSWORD is set — the app is "
                "open to anyone with the URL (core.auth._require_login_legacy()'s own "
                "documented open-door state).",
            )
        else:
            report.add("OK", "Legacy shared-password auth is configured (APP_PASSWORD is set).")

    # --- Credential encryption -------------------------------------------
    any_tenant_credential_flag = v2_auth or _flag("LEADLENS_V2_TENANT_CONTEXT_ENABLED")
    encryption_key = os.getenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if any_tenant_credential_flag and not encryption_key:
        report.add(
            "WARN",
            "V2 auth/tenant-context is on but LEADLENS_CREDENTIAL_ENCRYPTION_KEY is not "
            "set — any organization-scoped WhatsApp/Gmail/Calendar credential configured "
            "via services.integration_credentials will fail to encrypt/decrypt.",
        )
    elif encryption_key:
        report.add("OK", "LEADLENS_CREDENTIAL_ENCRYPTION_KEY is set.")

    # --- Unsafe flag combinations -----------------------------------------
    multi_org_intent = _flag("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED") or _flag(
        "LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED"
    )
    if multi_org_intent and not _flag("LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED"):
        report.add(
            "FAIL",
            "Multi-org mode appears enabled (CRM-tenant-authoritative and/or "
            "scheduler-multi-org) but LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED is "
            "off — the live audit view (Settings > Data protection) would read the "
            "single global legacy audit log, leaking every organization's audit trail "
            "into every other organization's view. See docs/V2_PHASE8_SAAS_ONBOARDING.md.",
        )
    if multi_org_intent and not _flag("LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED"):
        report.add(
            "WARN",
            "Multi-org mode appears enabled but LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED "
            "is off — company/clinic settings remain a single global object shared by "
            "every organization.",
        )
    if multi_org_intent and not v2_auth:
        report.add(
            "FAIL",
            "Multi-org mode appears enabled but LEADLENS_V2_AUTH_ENABLED is off — "
            "without real per-user login, there is no live session to resolve "
            "\"which organization\" for any of the tenant-authoritative code paths, "
            "so they will silently fall back to the single transitional default "
            "organization for every request.",
        )

    # --- Jarvis / LLM -------------------------------------------------------
    if os.getenv("OPENAI_API_KEY", "").strip():
        report.add("OK", "OPENAI_API_KEY is set — Jarvis can generate real responses.")
    else:
        report.add(
            "WARN",
            "OPENAI_API_KEY is not set — Jarvis falls back to templated/canned "
            "responses (see services/ai.py). Not a hard failure; some deployments "
            "may intentionally run without it during setup.",
        )

    return report
