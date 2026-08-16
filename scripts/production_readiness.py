"""Phase 9 — the single, safe production-readiness command.

Aggregates every read-only Phase 9 check into one PASS/WARN/FAIL report:

    - startup configuration validation (core/config_validation.py)
    - health check (scripts/health_check.py)
    - migration drift (Alembic head vs database)
    - multi-org tenant integrity + readiness gate (scripts/verify_multi_org_readiness.py)

Never mutates data, never sends a real WhatsApp/Gmail/Calendar message,
never prints a secret value. Safe to run against a real production
database as a pre-deploy/post-deploy check.

Usage:

    python scripts/production_readiness.py
    python scripts/production_readiness.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
_SEVERITY = {PASS: 0, WARN: 1, FAIL: 2}


def _worst(levels: list[str]) -> str:
    if not levels:
        return PASS
    return max(levels, key=lambda level: _SEVERITY.get(level, 0))


def _section_config() -> tuple[str, list[str]]:
    from core.config_validation import validate_configuration

    report = validate_configuration()
    lines = [f"  [{f.level}] {f.message}" for f in report.findings]
    level = FAIL if report.has_fail else (WARN if report.has_warn else PASS)
    return level, lines


def _section_health() -> tuple[str, list[str]]:
    from scripts.health_check import UNHEALTHY, run_health_check

    report = run_health_check()
    lines = [f"  [{o.status}] {o.name}: {o.detail}" for o in report.outcomes]
    level = FAIL if report.overall == UNHEALTHY else (WARN if report.overall == "DEGRADED" else PASS)
    return level, lines


def _section_integration_credentials() -> tuple[str, list[str]]:
    """Phase 9.1: a dedicated section so a credential-specific finding
    (a stored-but-undecryptable integration, for any organization, not
    just the transitional default) is never buried inside the 10-line
    general health section or masked by an unrelated failing check —
    see docs/V2_PHASE9_PRODUCTION_HARDENING.md's Phase 9.1 addendum."""
    try:
        from sqlalchemy.orm import Session

        from core.db.session import get_database_url, make_engine
        from services.integration_credentials import (
            assess_integration_health,
            credential_encryption_key_required,
        )

        engine = make_engine(get_database_url())
        try:
            with Session(engine) as session:
                required = credential_encryption_key_required(session)
                entries = assess_integration_health(session)
        finally:
            engine.dispose()

        lines: list[str] = [f"  master key required by stored data: {required}"]
        levels: list[str] = []

        configured = [e for e in entries if e.status != "UNCONFIGURED"]
        broken = [e for e in entries if e.decryptable is False]
        unconfigured = [e for e in entries if e.status == "UNCONFIGURED"]

        lines.append(
            f"  {len(configured)} configured, {len(unconfigured)} unconfigured (optional, not a health "
            f"issue on its own), across {len({e.organization_id for e in entries})} active organization(s)"
        )
        if broken:
            levels.append(FAIL)
            for e in broken:
                lines.append(
                    f"  [FAIL] {e.organization_slug}/{e.provider} status={e.status} "
                    f"undecryptable (error_category={e.error_category})"
                )
        else:
            lines.append("  [PASS] no configured integration is undecryptable")

        return _worst(levels or [PASS]), lines
    except Exception as exc:  # noqa: BLE001
        return FAIL, [f"  [FAIL] integration credential health check errored: {type(exc).__name__}: {exc}"]


def _section_migration_drift() -> tuple[str, list[str]]:
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
                return WARN, ["  [WARN] database has not been migrated yet (no alembic_version table)"]
            with engine.connect() as conn:
                current = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            if current == head_revision:
                return PASS, [f"  [PASS] database schema is at head ({current})"]
            return FAIL, [f"  [FAIL] database is at {current}, code expects head {head_revision} — run `alembic upgrade head`"]
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - readiness check must never crash
        return FAIL, [f"  [FAIL] migration drift check errored: {type(exc).__name__}: {exc}"]


def _section_tenant_integrity() -> tuple[str, list[str]]:
    try:
        from sqlalchemy.orm import Session

        from core.db.models.organization import Organization
        from core.db.session import get_database_url, make_engine
        from scripts.verify_multi_org_readiness import (
            cross_org_fk_check,
            membership_orphan_check,
            multi_org_readiness_gate,
            shadow_sync_health,
        )

        engine = make_engine(get_database_url())
        try:
            with Session(engine) as session:
                organizations_count = session.query(Organization).count()
                fk_problems = cross_org_fk_check(session)
                orphan_problems = membership_orphan_check(session)
                sync_health = shadow_sync_health(session)
                gate_findings = multi_org_readiness_gate(
                    organizations_count,
                    unresolved_shadow_write_failures=sync_health["unresolved_shadow_write_failures"],
                )
        finally:
            engine.dispose()

        lines = [f"  organizations: {organizations_count}"]
        levels = []
        if fk_problems:
            levels.append(FAIL)
            lines.append(f"  [FAIL] cross-org FK check: {len(fk_problems)} problem(s)")
        else:
            lines.append("  [PASS] cross-org FK check: clean")
        if orphan_problems:
            levels.append(FAIL)
            lines.append(f"  [FAIL] membership orphan check: {len(orphan_problems)} problem(s)")
        else:
            lines.append("  [PASS] membership orphan check: clean")
        lines.append(
            f"  shadow-sync: {sync_health['unresolved_shadow_write_failures']} unresolved failure(s), "
            f"{sync_health['read_mismatches_recorded']} read mismatch(es)"
        )
        for level, message in gate_findings:
            lines.append(f"  [{level}] {message}")
            levels.append(level)
        return _worst(levels or [PASS]), lines
    except Exception as exc:  # noqa: BLE001
        return FAIL, [f"  [FAIL] tenant integrity check errored: {type(exc).__name__}: {exc}"]


SECTIONS = (
    ("configuration", _section_config),
    ("health", _section_health),
    ("integration_credentials", _section_integration_credentials),
    ("migration_drift", _section_migration_drift),
    ("tenant_integrity", _section_tenant_integrity),
)


def run_production_readiness() -> tuple[str, dict[str, tuple[str, list[str]]]]:
    results: dict[str, tuple[str, list[str]]] = {}
    for name, fn in SECTIONS:
        results[name] = fn()
    overall = _worst([level for level, _ in results.values()])
    return overall, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a human report.")
    args = parser.parse_args()

    overall, results = run_production_readiness()

    if args.json:
        print(json.dumps(
            {
                "status": overall,
                "sections": {name: {"status": level, "detail": lines} for name, (level, lines) in results.items()},
            },
            indent=2,
        ))
    else:
        print(f"Production readiness: {overall}\n")
        for name, (level, lines) in results.items():
            print(f"=== {name} [{level}] ===")
            for line in lines:
                print(line)
            print()

    return 1 if overall == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
