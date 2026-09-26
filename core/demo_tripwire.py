"""Public-demo startup tripwire — fail closed if the environment does not
look like an isolated demo deployment.

Physical isolation (a separate app, database and OpenAI project) is the
real protection. This is the backstop for the one thing code can catch: an
operator pasting a production value into the demo's secrets. It refuses to
let the demo boot when it sees any of:

  * a provider / production secret in the environment (WhatsApp, Gmail,
    Google, the credential-encryption key, the legacy shared password, or
    any MASTER_* / CLIENT*_ deployment secret),
  * a database that is not PostgreSQL (the demo uses its own persistent
    Postgres, never SQLite and never a shared local file),
  * V2 auth or the tenant flags off (RBAC and tenant scoping are what the
    demo relies on),
  * a default-organization slug other than the demo's (the resolver would
    otherwise auto-create a stray "default-clinic" tenant),
  * a database that already contains a non-demo organization, or a legacy
    company profile that is not marked as the demo's — which is exactly
    what a production database would look like.

It reports NAMES of offending variables and counts, never values, and the
public UI shows visitors only a generic message; details go to the log.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from core.demo_mode import DEMO_ORG_SLUG

# Any env var starting with one of these is a provider/production secret.
FORBIDDEN_ENV_PREFIXES = ("WHATSAPP_", "GMAIL_", "GOOGLE_", "MASTER_", "CLIENT1_", "CLIENT_")
# Exact names that must never be present in a demo deployment.
FORBIDDEN_ENV_NAMES = (
    "LEADLENS_CREDENTIAL_ENCRYPTION_KEY",
    "APP_PASSWORD",
    "APP_PASSWORD_RECEPTIONIST",
    "APP_USER_ID_RECEPTIONIST",
)
# Flags that must be ON in a demo deployment.
REQUIRED_TRUE_FLAGS = (
    "LEADLENS_V2_AUTH_ENABLED",
    "LEADLENS_V2_TENANT_CONTEXT_ENABLED",
    "LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED",
    "LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED",
    "LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED",
)
# Flags that must NOT be on.
FORBIDDEN_TRUE_FLAGS = ("LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED", "LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED")

_TRUE = {"1", "true", "yes"}


class DemoEnvironmentError(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(problems))


def _is_true(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in _TRUE


def _is_postgres_url(url: str) -> bool:
    """Plain PostgreSQL URLs only. A driver-qualified scheme such as
    postgresql+psycopg:// is refused on purpose: the legacy memory_store
    (core/memory.py) passes DATABASE_URL straight to psycopg2, which cannot
    parse it. SQLAlchemy picks the right driver from a plain postgresql://
    URL by itself (psycopg2 on SQLAlchemy 2.0, psycopg on 2.1)."""
    return url.strip().lower().startswith(("postgresql://", "postgres://"))


def forbidden_env_variables(env: Mapping[str, str] | None = None) -> list[str]:
    """Names (never values) of provider / production variables that are set.
    Shared by the startup tripwire and the operator seed/reset CLI."""
    env = os.environ if env is None else env
    return sorted(
        name for name, value in env.items()
        if str(value).strip()
        and (name.startswith(FORBIDDEN_ENV_PREFIXES) or name in FORBIDDEN_ENV_NAMES)
    )


def environment_problems(env: Mapping[str, str] | None = None) -> list[str]:
    """Pure check of the process environment. Returns human-readable
    problems (variable NAMES only — never a value)."""
    env = os.environ if env is None else env
    problems: list[str] = []

    if not _is_true(env, "LEADLENS_DEMO_MODE"):
        problems.append("LEADLENS_DEMO_MODE is not set")

    present = forbidden_env_variables(env)
    if present:
        problems.append("forbidden provider/production variable(s) present: " + ", ".join(present))

    for name in FORBIDDEN_TRUE_FLAGS:
        if _is_true(env, name):
            problems.append(f"{name} must not be enabled in the demo")
    for name in REQUIRED_TRUE_FLAGS:
        if not _is_true(env, name):
            problems.append(f"{name} must be enabled in the demo")

    database_url = str(env.get("LEADLENS_V2_DATABASE_URL", "")).strip() or str(env.get("DATABASE_URL", "")).strip()
    if not database_url:
        problems.append("DATABASE_URL is not set (the demo requires its own PostgreSQL database)")
    elif not _is_postgres_url(database_url):
        problems.append(
            "the database URL must be a plain postgresql:// URL (not SQLite, and no driver suffix such as "
            "+psycopg — the legacy store cannot parse those)"
        )
    legacy_url = str(env.get("DATABASE_URL", "")).strip()
    if legacy_url and str(env.get("LEADLENS_V2_DATABASE_URL", "")).strip() and legacy_url != str(env["LEADLENS_V2_DATABASE_URL"]).strip():
        problems.append("DATABASE_URL and LEADLENS_V2_DATABASE_URL point at different databases")

    if str(env.get("LEADLENS_DEFAULT_ORG_SLUG", "")).strip() != DEMO_ORG_SLUG:
        problems.append(f"LEADLENS_DEFAULT_ORG_SLUG must be {DEMO_ORG_SLUG!r}")

    if not str(env.get("OPENAI_API_KEY", "")).strip():
        problems.append("OPENAI_API_KEY is not set (use the dedicated demo-only key)")
    return problems


def database_problems(session, legacy_company: Mapping | None = None) -> list[str]:
    """Checks the connected database. An EMPTY database is fine here (it is
    simply not seeded yet — see demo_seeded()); one that holds any non-demo
    organization, or a legacy company profile not marked as the demo's, is
    refused."""
    from core.db.models.organization import Organization

    problems: list[str] = []
    non_demo = session.query(Organization).filter(Organization.is_demo.is_(False)).count()
    if non_demo:
        problems.append(f"the database contains {non_demo} non-demo organization(s)")
    company = dict(legacy_company or {})
    if company and company.get("is_demo") is not True:
        problems.append("the legacy company profile is not marked as the demo's")
    return problems


def demo_seeded(session) -> bool:
    """True once the demo organization and its demo viewer exist."""
    from core.db.models.identity import Membership, MembershipRole, MembershipStatus
    from core.db.models.organization import Organization

    org = session.query(Organization).filter(
        Organization.slug == DEMO_ORG_SLUG, Organization.is_demo.is_(True)
    ).one_or_none()
    if org is None:
        return False
    return (
        session.query(Membership)
        .filter(
            Membership.organization_id == org.id,
            Membership.role == MembershipRole.DEMO_VIEWER,
            Membership.status == MembershipStatus.ACTIVE,
        )
        .count()
        > 0
    )


def enforce_environment(env: Mapping[str, str] | None = None) -> None:
    """Raises DemoEnvironmentError if the environment is unsafe. Only
    meaningful when demo mode is on; callers gate on demo_mode_enabled()."""
    problems = environment_problems(env)
    if problems:
        raise DemoEnvironmentError(problems)
