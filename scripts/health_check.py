"""Phase 9 — lightweight health/status check.

Read-only, safe to run repeatedly (e.g. from a hosting platform's health
probe or a cron-based alert) — never mutates data, never sends a real
WhatsApp/Gmail/Calendar message, never prints a secret value.

Usage:

    python scripts/health_check.py            # human-readable
    python scripts/health_check.py --json      # machine-readable

Exit code: 0 for HEALTHY or DEGRADED, 1 for UNHEALTHY — matches the
common health-probe convention (DEGRADED still serves traffic, so it
isn't a probe failure; UNHEALTHY should stop routing traffic to this
instance).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
UNHEALTHY = "UNHEALTHY"

_SEVERITY = {HEALTHY: 0, DEGRADED: 1, UNHEALTHY: 2}


@dataclass
class CheckOutcome:
    name: str
    status: str
    detail: str = ""


@dataclass
class HealthReport:
    outcomes: list[CheckOutcome] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.outcomes.append(CheckOutcome(name, status, detail))

    @property
    def overall(self) -> str:
        if not self.outcomes:
            return UNHEALTHY
        worst = max(self.outcomes, key=lambda o: _SEVERITY[o.status])
        return worst.status


def check_process() -> CheckOutcome:
    return CheckOutcome("application_process", HEALTHY, "process is running (this script executed)")


def check_database() -> CheckOutcome:
    try:
        from core.db.session import get_database_url, make_engine

        url = get_database_url()
        engine = make_engine(url)
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return CheckOutcome("database_connectivity", HEALTHY, "connected")
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - health check must never crash
        return CheckOutcome("database_connectivity", UNHEALTHY, f"{type(exc).__name__}: {exc}")


def check_migrations() -> CheckOutcome:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import inspect

        from core.db.session import get_database_url, make_engine

        cfg = Config(str(ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        head_revision = script.get_current_head()

        engine = make_engine(get_database_url())
        try:
            inspector = inspect(engine)
            if "alembic_version" not in inspector.get_table_names():
                return CheckOutcome(
                    "migrations", DEGRADED,
                    "no alembic_version table yet — database has not been migrated "
                    "(fine for a brand-new deployment before its first `alembic upgrade head`)",
                )
            with engine.connect() as conn:
                current = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            if current == head_revision:
                return CheckOutcome("migrations", HEALTHY, f"at head ({current})")
            return CheckOutcome(
                "migrations", DEGRADED,
                f"database is at {current}, code expects head {head_revision} — run `alembic upgrade head`",
            )
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001
        return CheckOutcome("migrations", UNHEALTHY, f"{type(exc).__name__}: {exc}")


def check_legacy_memory_store() -> CheckOutcome:
    try:
        import core.memory as business_memory

        business_memory.load_memory()
        return CheckOutcome("legacy_memory_store", HEALTHY, "reachable")
    except Exception as exc:  # noqa: BLE001
        return CheckOutcome("legacy_memory_store", DEGRADED, f"{type(exc).__name__}: {exc}")


def check_organization_resolution() -> CheckOutcome:
    try:
        from core.db.session import get_database_url, make_engine, session_scope
        from core.identity.default_organization import resolve_default_organization_id

        engine = make_engine(get_database_url())
        try:
            with session_scope(engine) as session:
                org_id = resolve_default_organization_id(session)
            return CheckOutcome("organization_resolution", HEALTHY, f"resolves (id={org_id})")
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001
        return CheckOutcome("organization_resolution", UNHEALTHY, f"{type(exc).__name__}: {exc}")


def check_scheduler_readiness() -> CheckOutcome:
    try:
        from scheduler.run_scheduled_checks import CHECKS, resolve_scheduler_organizations

        if not CHECKS:
            return CheckOutcome("scheduler_readiness", DEGRADED, "no checks registered")
        organizations = resolve_scheduler_organizations()
        if not organizations:
            return CheckOutcome(
                "scheduler_readiness", DEGRADED,
                f"{len(CHECKS)} check(s) registered, but no organization resolved for a run",
            )
        return CheckOutcome(
            "scheduler_readiness", HEALTHY,
            f"{len(CHECKS)} check(s) registered, {len(organizations)} organization(s) eligible",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckOutcome("scheduler_readiness", UNHEALTHY, f"{type(exc).__name__}: {exc}")


def _integration_health_snapshot():
    """Returns (required: bool, entries: list[IntegrationHealthEntry]) from
    actual database state, or (None, None) if the check itself couldn't
    run (DB unreachable, etc. — caller decides how to treat that). Both
    check_credential_encryption() and check_integration_configuration()
    derive their verdict from this SAME real-decryption-attempt data
    (services.integration_credentials.assess_integration_health()) so
    they can never disagree with each other the way a syntax-only key
    check could — a "wrong but well-formed" key must be caught by
    actually attempting decryption, not by checking presence alone."""
    try:
        from core.db.session import get_database_url, make_engine, session_scope
        from services.integration_credentials import (
            assess_integration_health,
            credential_encryption_key_required,
        )

        engine = make_engine(get_database_url())
        try:
            with session_scope(engine) as session:
                required = credential_encryption_key_required(session)
                entries = assess_integration_health(session)
                return required, entries
        finally:
            engine.dispose()
    except Exception:  # noqa: BLE001 - health check must never crash
        return None, None


def check_credential_encryption() -> CheckOutcome:
    """Phase 9.1: severity is driven by whether the database ACTUALLY
    holds a stored encrypted credential that needs this key, AND whether
    the currently-configured key actually decrypts it — never by syntax
    validity alone, and never by whether a V2 feature flag happens to be
    on. A key that's missing, invalid, OR simply WRONG (well-formed, but
    not the key that encrypted the stored data) while real credentials
    are stored is a genuine operational failure regardless of flags (the
    Phase 9 audit's confirmed defect, plus the "wrong key" case a
    presence-only check cannot catch)."""
    key = os.getenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if key:
        try:
            from cryptography.fernet import Fernet

            Fernet(key.encode())
        except Exception:  # noqa: BLE001
            return CheckOutcome("credential_encryption_key", UNHEALTHY, "present but not a valid Fernet key")

    required, entries = _integration_health_snapshot()

    if required is None:
        return CheckOutcome(
            "credential_encryption_key", DEGRADED,
            "could not determine whether stored credentials require this key (database unreachable) — "
            "treat as unresolved until the database check itself passes",
        )

    # Both categories are surfaced here, deliberately: Fernet's
    # authenticated encryption cannot distinguish "wrong key" from
    # "corrupted ciphertext" at the API level (both fail the same HMAC
    # check and raise InvalidToken) — key_unavailable means the env var
    # itself is missing/malformed, decryption_failed means a
    # syntactically-valid key didn't decrypt the stored data, which is
    # either the wrong key OR corrupted data. Either way, this
    # environment's current key cannot currently produce working
    # credentials for the affected row(s) — a real operational failure,
    # not a "not required" configuration gap.
    undecryptable = [e for e in entries if e.error_category in ("key_unavailable", "decryption_failed")]
    if undecryptable:
        who = ", ".join(f"{e.organization_slug}/{e.provider}" for e in undecryptable[:5])
        return CheckOutcome(
            "credential_encryption_key", UNHEALTHY,
            f"the configured key cannot decrypt {len(undecryptable)} stored credential(s): {who}. "
            "This is a real operational failure — the key is either missing, wrong (does not match "
            "the key that encrypted this data), or the stored ciphertext itself is corrupted; Fernet's "
            "authenticated encryption cannot distinguish the last two automatically.",
        )

    if required:
        return CheckOutcome("credential_encryption_key", HEALTHY, "present, well-formed, and verified against stored credentials")
    if key:
        return CheckOutcome("credential_encryption_key", HEALTHY, "present and well-formed")

    v2_features_on = any(
        os.getenv(name, "").strip().lower() in {"1", "true", "yes"}
        for name in ("LEADLENS_V2_AUTH_ENABLED", "LEADLENS_V2_TENANT_CONTEXT_ENABLED")
    )
    if v2_features_on:
        return CheckOutcome(
            "credential_encryption_key", DEGRADED,
            "not set — organization-scoped integration credentials cannot be configured",
        )
    return CheckOutcome(
        "credential_encryption_key", HEALTHY,
        "not required (no organization has a stored credential, and V2 tenant features are off)",
    )


def check_integration_configuration() -> CheckOutcome:
    """Phase 9.1: enumerates every ACTIVE organization's integrations —
    not just the transitional default — so a broken credential belonging
    to any non-default organization is no longer invisible here (the
    Phase 9 audit's second confirmed defect). Reports counts/statuses
    only — never a secret value; safe organization slugs and provider
    names only for anything flagged unhealthy."""
    try:
        from core.db.session import get_database_url, make_engine, session_scope
        from services.integration_credentials import assess_integration_health

        engine = make_engine(get_database_url())
        try:
            with session_scope(engine) as session:
                entries = assess_integration_health(session)
        finally:
            engine.dispose()

        configured = [e for e in entries if e.status != "UNCONFIGURED"]
        broken = [e for e in entries if e.decryptable is False]
        if broken:
            broken_desc = ", ".join(f"{e.organization_slug}/{e.provider}" for e in broken[:5])
            more = f" (+{len(broken) - 5} more)" if len(broken) > 5 else ""
            return CheckOutcome(
                "integration_configuration", UNHEALTHY,
                f"{len(broken)} configured integration(s) undecryptable: {broken_desc}{more}",
            )
        return CheckOutcome(
            "integration_configuration", HEALTHY,
            f"{len(configured)} configured integration(s) across {len({e.organization_id for e in entries})} "
            "active organization(s) — all decryptable or not requiring decryption "
            "(unconfigured optional providers are not a health issue)",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckOutcome("integration_configuration", DEGRADED, f"{type(exc).__name__}: {exc}")


def check_jarvis_configuration() -> CheckOutcome:
    if os.getenv("OPENAI_API_KEY", "").strip():
        return CheckOutcome("jarvis_llm_configuration", HEALTHY, "OPENAI_API_KEY present")
    return CheckOutcome("jarvis_llm_configuration", DEGRADED, "OPENAI_API_KEY not set — Jarvis uses templated fallback responses")


def check_migration_flags() -> CheckOutcome:
    from core.config_validation import validate_configuration

    report = validate_configuration()
    if report.has_fail:
        return CheckOutcome("configuration", UNHEALTHY, f"{sum(f.level == 'FAIL' for f in report.findings)} FAIL finding(s) — see scripts/production_readiness.py")
    if report.has_warn:
        return CheckOutcome("configuration", DEGRADED, f"{sum(f.level == 'WARN' for f in report.findings)} WARN finding(s) — see scripts/production_readiness.py")
    return CheckOutcome("configuration", HEALTHY, "no findings")


def run_health_check() -> HealthReport:
    report = HealthReport()
    for outcome in (
        check_process(),
        check_database(),
        check_migrations(),
        check_legacy_memory_store(),
        check_organization_resolution(),
        check_scheduler_readiness(),
        check_credential_encryption(),
        check_integration_configuration(),
        check_jarvis_configuration(),
        check_migration_flags(),
    ):
        report.outcomes.append(outcome)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a human report.")
    args = parser.parse_args()

    report = run_health_check()

    if args.json:
        print(json.dumps({
            "status": report.overall,
            "checks": [{"name": o.name, "status": o.status, "detail": o.detail} for o in report.outcomes],
        }, indent=2))
    else:
        print(f"Overall status: {report.overall}\n")
        for outcome in report.outcomes:
            print(f"  [{outcome.status:9s}] {outcome.name}: {outcome.detail}")

    return 1 if report.overall == UNHEALTHY else 0


if __name__ == "__main__":
    raise SystemExit(main())
