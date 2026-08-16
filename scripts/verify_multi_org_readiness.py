"""Phase 8 — multi-organization readiness verifier.

Safe, read-only report of organization-level metadata: counts and
statuses, never patient-sensitive data (no names, emails, phone numbers,
or financial line items are printed — only row counts and boolean/enum
states). Run this after provisioning a second organization to confirm
the deployment is genuinely multi-tenant-ready before onboarding a real
second clinic.

Usage:

    python scripts/verify_multi_org_readiness.py
    python scripts/verify_multi_org_readiness.py --organization-id 2
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import func  # noqa: E402

from core.db.models.clinic import (  # noqa: E402
    Appointment,
    CorporateClient,
    Lead,
    Package,
    PackageTemplate,
    Patient,
    Payment,
    ProgressNote,
    Therapist,
)
from core.db.models.identity import Membership, MembershipStatus, User  # noqa: E402
from core.db.models.integration import OrganizationIntegration  # noqa: E402
from core.db.models.jarvis import JarvisLearningRecord  # noqa: E402
from core.db.models.operations import Approval, ExecutionQueueItem, SecurityAuditEvent  # noqa: E402
from core.db.models.organization import Organization, OrganizationSettings  # noqa: E402
from core.db.models.shadow_sync import ReadMismatch, ShadowSyncFailure  # noqa: E402
from core.db.session import get_database_url, make_engine, session_scope  # noqa: E402

_CRM_MODELS = {
    "patients": Patient,
    "appointments": Appointment,
    "packages": Package,
    "package_templates": PackageTemplate,
    "payments": Payment,
    "therapists": Therapist,
    "progress_notes": ProgressNote,
    "leads": Lead,
    "corporate_clients": CorporateClient,
}


def _count(session, model, organization_id: int) -> int:
    return session.query(func.count(model.id)).filter(model.organization_id == organization_id).scalar() or 0


def report_organization(session, org: Organization) -> dict:
    settings = (
        session.query(OrganizationSettings)
        .filter(OrganizationSettings.organization_id == org.id)
        .one_or_none()
    )
    active_memberships = (
        session.query(func.count(Membership.id))
        .filter(Membership.organization_id == org.id, Membership.status == MembershipStatus.ACTIVE)
        .scalar()
        or 0
    )
    crm_counts = {name: _count(session, model, org.id) for name, model in _CRM_MODELS.items()}
    integrations = (
        session.query(OrganizationIntegration.provider, OrganizationIntegration.status)
        .filter(OrganizationIntegration.organization_id == org.id)
        .all()
    )
    approvals = session.query(func.count(Approval.id)).filter(Approval.organization_id == org.id).scalar() or 0
    queue_items = session.query(func.count(ExecutionQueueItem.id)).filter(ExecutionQueueItem.organization_id == org.id).scalar() or 0
    audit_events = session.query(func.count(SecurityAuditEvent.id)).filter(SecurityAuditEvent.organization_id == org.id).scalar() or 0
    jarvis_memory = session.query(func.count(JarvisLearningRecord.id)).filter(JarvisLearningRecord.organization_id == org.id).scalar() or 0

    return {
        "organization_id": org.id,
        "slug": org.slug,
        "status": org.status.value,
        "settings_exist": settings is not None,
        "automations_enabled": bool(settings.automations_enabled) if settings else False,
        "active_memberships": active_memberships,
        "crm_row_counts": crm_counts,
        "integration_statuses": {provider.value: status.value for provider, status in integrations},
        "approval_count": approvals,
        "execution_queue_count": queue_items,
        "audit_event_count": audit_events,
        "jarvis_memory_row_count": jarvis_memory,
    }


def cross_org_fk_check(session) -> list[str]:
    """Spot-checks that no CRM child row's parent lives in a different
    organization than the child itself — the composite-FK design in
    core/db/models/clinic.py should make this structurally impossible,
    so this exists as a defense-in-depth sanity check, not the primary
    guarantee."""
    problems: list[str] = []
    for appt in session.query(Appointment).all():
        patient = session.get(Patient, appt.patient_id)
        if patient is not None and patient.organization_id != appt.organization_id:
            problems.append(f"appointment {appt.id} organization_id={appt.organization_id} references patient in organization_id={patient.organization_id}")
    for pkg in session.query(Package).all():
        patient = session.get(Patient, pkg.patient_id)
        if patient is not None and patient.organization_id != pkg.organization_id:
            problems.append(f"package {pkg.id} organization_id={pkg.organization_id} references patient in organization_id={patient.organization_id}")
    for pay in session.query(Payment).all():
        patient = session.get(Patient, pay.patient_id)
        if patient is not None and patient.organization_id != pay.organization_id:
            problems.append(f"payment {pay.id} organization_id={pay.organization_id} references patient in organization_id={patient.organization_id}")
    return problems


def membership_orphan_check(session) -> list[str]:
    """Phase 9 §12: memberships must always resolve to a real user and a
    real organization — the FK constraints already make this impossible
    in a healthy database, so this is defense-in-depth, same spirit as
    cross_org_fk_check(). Reports counts/ids only, never patient data."""
    problems: list[str] = []
    for membership in session.query(Membership).all():
        if session.get(User, membership.user_id) is None:
            problems.append(f"membership {membership.id} references missing user_id={membership.user_id}")
        if session.get(Organization, membership.organization_id) is None:
            problems.append(f"membership {membership.id} references missing organization_id={membership.organization_id}")
    return problems


def shadow_sync_health(session) -> dict:
    """Phase 9 §12: unresolved Phase 3/4 shadow-write/read-mismatch
    counts — an operator-facing signal of CRM legacy/relational parity
    health while dual-write/read-cutover flags are in use. Counts only;
    see scripts/repair_v2_crm.py and scripts/verify_v2_crm_parity.py for
    the full per-record remediation tooling this summarizes."""
    unresolved_write_failures = (
        session.query(func.count(ShadowSyncFailure.id))
        .filter(ShadowSyncFailure.resolved.is_(False))
        .scalar()
        or 0
    )
    total_write_failures = session.query(func.count(ShadowSyncFailure.id)).scalar() or 0
    read_mismatches = session.query(func.count(ReadMismatch.id)).scalar() or 0
    return {
        "unresolved_shadow_write_failures": unresolved_write_failures,
        "total_shadow_write_failures": total_write_failures,
        "read_mismatches_recorded": read_mismatches,
    }


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def multi_org_readiness_gate(
    organizations_count: int, *, unresolved_shadow_write_failures: int = 0,
) -> list[tuple[str, str]]:
    """Phase 8.1 (extended Phase 9 §12): an explicit, honest readiness
    gate — no false PASS. Returns a list of (level, message) where level
    is "OK", "WARN", or "FAIL". A FAIL means this deployment cannot
    safely be used as a shared multi-org production database as it
    stands; a WARN flags a real limitation that is safe only because
    there is currently just one organization, or a known-unresolved
    operational issue worth an operator's attention."""
    findings: list[tuple[str, str]] = []

    audit_scoped = _env_flag("LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED")
    if organizations_count > 1 and not audit_scoped:
        findings.append((
            "FAIL",
            "LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED is off with more than one "
            "organization present — the live audit_rows() view (Settings > Data "
            "protection) reads the single GLOBAL legacy audit log, mixing every "
            "organization's audit events together for any viewer with audit.view.",
        ))
    elif not audit_scoped:
        findings.append((
            "WARN",
            "LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED is off — the live audit "
            "view reads the single global legacy audit log. Safe only because exactly "
            "one organization currently exists; turn this on before provisioning a "
            "second organization that will use the Settings > Data protection tab.",
        ))
    else:
        findings.append(("OK", "Live audit view (audit_rows()) is organization-scoped."))

    scheduler_multi_org = _env_flag("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED")
    try:
        from scheduler.run_scheduled_checks import CHECKS
        import inspect

        all_context_capable = all(
            "context" in inspect.signature(func).parameters for func in CHECKS
        )
    except Exception:
        all_context_capable = False
    if not all_context_capable:
        findings.append((
            "FAIL",
            "One or more scheduler check functions do not accept an explicit "
            "organization context — multi-org scheduler execution cannot be trusted.",
        ))
    elif organizations_count > 1 and not scheduler_multi_org:
        findings.append((
            "WARN",
            "Scheduler checks are organization-context-capable, but "
            "LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED is off — the scheduler still "
            "runs only against the single transitional default organization, not "
            "every eligible organization.",
        ))
    else:
        findings.append(("OK", "Scheduler checks accept an explicit organization context."))

    if organizations_count > 1:
        marketing_slug_configured = bool(os.environ.get("LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG", "").strip())
        if marketing_slug_configured:
            findings.append((
                "OK",
                "marketing-site/api/lead.py has LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG "
                "configured (Phase 9) — it resolves the exact organization this Vercel "
                "deployment's leads belong to and refuses to guess otherwise. Verify the "
                "configured slug matches the intended organization for THIS deployment.",
            ))
        else:
            findings.append((
                "FAIL",
                "marketing-site/api/lead.py has NO LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG "
                "configured and more than one organization exists — it refuses to write at "
                "all (Phase 8.1/9 safety guard — see AmbiguousMultiOrgDatabaseError) rather "
                "than guess which clinic a public lead belongs to. Public lead capture is "
                "UNSUPPORTED for this deployment until the slug is configured.",
            ))
    else:
        findings.append((
            "OK",
            "Only one organization present — marketing-site/api/lead.py resolves it "
            "unambiguously without any extra configuration.",
        ))

    if unresolved_shadow_write_failures > 0:
        findings.append((
            "WARN",
            f"{unresolved_shadow_write_failures} unresolved CRM shadow-write failure(s) — "
            "see scripts/repair_v2_crm.py to investigate/remediate.",
        ))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--organization-id", type=int, default=None, help="Report only this organization; default: all.")
    args = parser.parse_args()

    database_url = get_database_url()
    print(f"Target database: {database_url}\n")

    engine = make_engine(database_url)
    try:
        with session_scope(engine) as session:
            query = session.query(Organization)
            if args.organization_id is not None:
                query = query.filter(Organization.id == args.organization_id)
            organizations = query.order_by(Organization.id.asc()).all()

            print(f"Organizations: {len(organizations)}\n")
            for org in organizations:
                report = report_organization(session, org)
                print(f"--- Organization {report['organization_id']} ({report['slug']}) ---")
                print(f"  status: {report['status']}")
                print(f"  settings configured: {report['settings_exist']}")
                print(f"  automations enabled: {report['automations_enabled']}")
                print(f"  active memberships: {report['active_memberships']}")
                print(f"  CRM row counts: {report['crm_row_counts']}")
                print(f"  integration statuses: {report['integration_statuses'] or '(none configured)'}")
                print(f"  approvals: {report['approval_count']}")
                print(f"  execution queue items: {report['execution_queue_count']}")
                print(f"  audit events: {report['audit_event_count']}")
                print(f"  Jarvis memory rows: {report['jarvis_memory_row_count']}")
                print()

            problems = cross_org_fk_check(session)
            if problems:
                print(f"CROSS-ORG FK CHECK: FAILED ({len(problems)} problem(s)):")
                for problem in problems:
                    print(f"  - {problem}")
            else:
                print("Cross-org FK check: PASSED (no child row references a parent in a different organization).")

            orphan_problems = membership_orphan_check(session)
            if orphan_problems:
                print(f"MEMBERSHIP ORPHAN CHECK: FAILED ({len(orphan_problems)} problem(s)):")
                for problem in orphan_problems:
                    print(f"  - {problem}")
            else:
                print("Membership orphan check: PASSED (every membership resolves to a real user and organization).")

            sync_health = shadow_sync_health(session)
            print(
                f"Shadow-sync health: {sync_health['unresolved_shadow_write_failures']} unresolved write "
                f"failure(s) ({sync_health['total_shadow_write_failures']} total ever recorded), "
                f"{sync_health['read_mismatches_recorded']} read mismatch(es) recorded."
            )

            print("\n--- Multi-org production readiness gate ---")
            findings = multi_org_readiness_gate(
                len(organizations),
                unresolved_shadow_write_failures=sync_health["unresolved_shadow_write_failures"],
            )
            has_fail = False
            for level, message in findings:
                print(f"  [{level}] {message}")
                if level == "FAIL":
                    has_fail = True
    finally:
        engine.dispose()
    return 1 if has_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
