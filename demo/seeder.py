"""Seed and reset the public demo tenant.

Library code (no argument parsing, no environment access beyond what the
app's own modules already do) so it is fully testable. The operator-facing
command line lives in demo/operator_cli.py, which adds the target-database
safety checks and typed confirmation.

Design rules:

  * Data goes in through the app's REAL service layer (clinic_data_service,
    integration_manager_v21, jarvis_memory, ...), so the demo exercises the
    same code paths a real clinic does and can never drift from the product.
  * The seeder forces the tenant-authoritative modes and binds every service
    engine for the duration of the run, so it behaves the same regardless of
    the operator's shell environment, and restores everything afterwards.
  * Every relational delete is filtered by the demo organization's id and is
    refused unless that organization is is_demo with the demo slug. The
    reset additionally refuses to run at all if the database contains any
    non-demo organization or the legacy company profile is not marked as the
    demo's — the exact signature of a production database.
  * demo_usage (the LLM budget counters) is deliberately NOT reset: a reset
    must never refill the spending budget.
  * No external action is ever performed. Queued WhatsApp messages are only
    PREPARED (awaiting approval); nothing is sent, and demo mode refuses
    execution outright.
"""
from __future__ import annotations

import contextlib
import json
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

from sqlalchemy.orm import Session

from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import (
    Membership,
    MembershipRole,
    MembershipStatus,
    User,
    UserStatus,
)
from core.db.models.organization import Organization, OrganizationStatus
from core.demo_mode import (
    DEMO_ORG_NAME,
    DEMO_ORG_SLUG,
    DEMO_SEED_DIR,
    DEMO_USER_EMAIL,
    allow_demo_writes,
)
from core.demo_tripwire import database_problems
from demo.seed_data import DemoDataset, build_dataset

# Not a valid Argon2 hash, so verify_password() returns False for every
# possible input: the demo user has NO password, and can only be entered
# through the server-side demo entry flow.
UNUSABLE_PASSWORD_HASH = "!demo-account-has-no-password"

# Tables that survive a reset (identity rows are restored, not deleted;
# demo_usage is the LLM budget and must never be refilled by a reset).
_PROTECTED_TABLES = frozenset({"organizations", "users", "memberships", "demo_usage", "alembic_version"})


class DemoSeedError(RuntimeError):
    """A seed/reset precondition failed. Always raised BEFORE anything is
    written."""


# ---------------------------------------------------------------------------
# Environment: force tenant-authoritative modes and bind every service engine
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def seeding_environment(engine, *, seed_dir: Path = DEMO_SEED_DIR):
    """Temporarily point every service at `engine`, force the tenant-
    authoritative modes on, make the demo organization the transitional
    default, and redirect the legacy learning-memory file into `seed_dir`
    (never the checkout's data/learning file). Everything is restored on
    exit."""
    import core.identity.default_organization as default_org
    import services.crm_read_router as crm_router
    import services.jarvis_memory as jarvis_memory
    import services.platform_data as platform_data
    import services.relational_sync_service as relational_sync
    import services.security_service as security_service
    import services.tenant_operational_sync as operational_sync

    patches = [
        mock.patch.object(crm_router, "_ENGINE", engine),
        mock.patch.object(crm_router, "TENANT_AUTHORITATIVE_ENABLED", True),
        mock.patch.object(relational_sync, "_ENGINE", engine),
        mock.patch.object(operational_sync, "_ENGINE", engine),
        mock.patch.object(operational_sync, "TENANT_CONTEXT_ENABLED", True),
        mock.patch.object(jarvis_memory, "_ENGINE", engine),
        mock.patch.object(jarvis_memory, "JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED", True),
        mock.patch.object(jarvis_memory, "_DB_DISABLED", False),
        mock.patch.object(jarvis_memory, "STORE", Path(seed_dir) / "learning_memory.json"),
        mock.patch.object(platform_data, "_ENGINE", engine),
        mock.patch.object(platform_data, "ORG_SCOPED_SETTINGS_ENABLED", True),
        mock.patch.object(security_service, "_ENGINE", engine),
        mock.patch.object(default_org, "DEFAULT_ORGANIZATION_SLUG", DEMO_ORG_SLUG),
    ]
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        yield


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def ensure_demo_identity(session: Session) -> tuple[Organization, User, Membership]:
    """Idempotent get-or-create of the demo organization, the passwordless
    demo user, and its DEMO_VIEWER membership. Refuses to touch an existing
    organization that merely shares the demo slug but is not marked demo."""
    org = session.query(Organization).filter(Organization.slug == DEMO_ORG_SLUG).one_or_none()
    if org is None:
        org = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG, status=OrganizationStatus.ACTIVE, is_demo=True)
        session.add(org)
    elif not org.is_demo:
        raise DemoSeedError(
            f"an organization with slug {DEMO_ORG_SLUG!r} exists but is not marked is_demo — refusing to touch it"
        )
    org.name = DEMO_ORG_NAME
    org.status = OrganizationStatus.ACTIVE
    session.flush()

    user = session.query(User).filter(User.email == DEMO_USER_EMAIL).one_or_none()
    if user is None:
        user = User(email=DEMO_USER_EMAIL, password_hash=UNUSABLE_PASSWORD_HASH, status=UserStatus.ACTIVE)
        session.add(user)
    user.password_hash = UNUSABLE_PASSWORD_HASH  # never let it become a real credential
    user.status = UserStatus.ACTIVE
    session.flush()

    membership = (
        session.query(Membership)
        .filter(Membership.user_id == user.id, Membership.organization_id == org.id)
        .one_or_none()
    )
    if membership is None:
        membership = Membership(
            user_id=user.id, organization_id=org.id, role=MembershipRole.DEMO_VIEWER, status=MembershipStatus.ACTIVE
        )
        session.add(membership)
    membership.role = MembershipRole.DEMO_VIEWER
    membership.status = MembershipStatus.ACTIVE
    session.flush()
    return org, user, membership


def require_demo_org(org: Organization) -> None:
    if not (org.is_demo and org.slug == DEMO_ORG_SLUG):
        raise DemoSeedError("refusing to operate on an organization that is not the demo tenant")


# ---------------------------------------------------------------------------
# Wipe (strictly demo-organization-scoped)
# ---------------------------------------------------------------------------

def wipe_demo_tenant_rows(session: Session, org: Organization) -> dict[str, int]:
    """Delete every organization-scoped row belonging to the demo tenant —
    and ONLY the demo tenant (each DELETE is filtered by its organization
    id). Identity rows and the LLM usage counters are kept. Children are
    deleted before parents (metadata is dependency-sorted)."""
    require_demo_org(org)
    deleted: dict[str, int] = {}
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in _PROTECTED_TABLES or "organization_id" not in table.c:
            continue
        result = session.execute(table.delete().where(table.c.organization_id == org.id))
        if result.rowcount:
            deleted[table.name] = int(result.rowcount)
    return deleted


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

_ENTITY_ID_FIELD = {
    "therapists": "therapist_id", "package_templates": "template_id", "patients": "patient_id",
    "packages": "package_id", "appointments": "appointment_id", "payments": "payment_id",
    "progress_notes": "progress_id", "leads": "lead_id", "corporate_clients": "client_id",
}


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """Drop the dataset's private bookkeeping keys (`*_ref`, `_price`)."""
    return {k: v for k, v in row.items() if not k.endswith("_ref") and not k.startswith("_")}


def seed_demo_data(engine, org_id: int, dataset: DemoDataset, *, seed_dir: Path = DEMO_SEED_DIR) -> dict[str, int]:
    """Populate an EMPTY demo tenant. Call inside seeding_environment() and
    allow_demo_writes(); ensure_demo_identity() must already have run."""
    from core.identity import organization_profile_service
    from core.identity.tenant_context import ActorType, TenantContext
    from core.memory import (
        add_approval, add_daily_log, add_decision, add_memory_entry, add_task, save_company,
    )
    from core.db.session import session_scope
    from services import jarvis_memory
    from services.clinic_data_service import add_record
    from services.integration_manager_v21 import decide_item, prepare_execution

    ids: dict[str, list[str]] = {}

    def insert(entity: str, rows: list[dict[str, Any]], resolve) -> None:
        ids[entity] = []
        for row in rows:
            record = add_record(entity, resolve(_clean(row), row), organization_id=org_id)
            ids[entity].append(record[_ENTITY_ID_FIELD[entity]])

    # Settings first (org-scoped OrganizationSettings) and the legacy company profile
    # (which carries the is_demo marker the tripwire looks for). Automations stay OFF.
    with session_scope(engine) as session:
        organization_profile_service.save_settings(session, org_id, dict(dataset.settings))
        organization_profile_service.set_automations_enabled(session, org_id, False)
    save_company(dict(dataset.company))

    # CRM, in dependency order.
    insert("therapists", dataset.therapists, lambda clean, raw: clean)
    insert("package_templates", dataset.package_templates, lambda clean, raw: clean)
    insert("patients", dataset.patients, lambda clean, raw: clean)
    insert("packages", dataset.packages,
           lambda clean, raw: {**clean, "patient_id": ids["patients"][raw["patient_ref"]]})
    insert("appointments", dataset.appointments,
           lambda clean, raw: {**clean, "patient_id": ids["patients"][raw["patient_ref"]],
                               "therapist_id": ids["therapists"][raw["therapist_ref"]]})
    insert("payments", dataset.payments,
           lambda clean, raw: {**clean, "patient_id": ids["patients"][raw["patient_ref"]],
                               "package_id": ids["packages"][raw["package_ref"]]})
    insert("progress_notes", dataset.progress_notes,
           lambda clean, raw: {**clean, "patient_id": ids["patients"][raw["patient_ref"]],
                               "therapist_id": ids["therapists"][raw["therapist_ref"]]})
    insert("leads", dataset.leads, lambda clean, raw: clean)
    insert("corporate_clients", dataset.corporate_clients, lambda clean, raw: clean)

    # Legacy business-memory sections (approvals, tasks, logs, reports).
    for task in dataset.tasks:
        add_task(task["title"], task["department"], task["priority"])
    for decision in dataset.decisions:
        add_decision(decision["title"], decision["reason"], decision["impact"])
    for approval in dataset.extra_approvals:
        add_approval(approval["title"], approval["department"], approval["risk_level"])
    for summary in dataset.daily_logs:
        add_daily_log(summary)
    for report in dataset.reports:
        add_memory_entry("reports", {**report, "source": "scheduler"})

    # The approval queue: PREPARED actions only. Nothing is ever sent.
    context = TenantContext(organization_id=org_id, actor_type=ActorType.SCHEDULER, source="demo_seed")
    queued = 0
    for action in dataset.workflow_actions:
        item = prepare_execution(
            action["provider"], action["action"], action["payload"], title=action["title"],
            impact=action["impact"], recommendation_id=f"scheduler:{action['check']}", tenant_context=context,
        )
        queued += 1
        if action.get("decision"):
            decide_item(item["id"], action["decision"])

    # Jarvis learning memory (database primary + the synthetic JSON file in seed_dir).
    recommendations = 0
    for rec in dataset.recommendations:
        tracked = jarvis_memory.track_recommendation(
            rec["question"], rec["recommendation"], agents=rec["agents"], tags=rec["tags"]
        )
        recommendations += 1
        if rec.get("outcome"):
            outcome = rec["outcome"]
            jarvis_memory.record_recommendation_outcome(
                tracked["id"], outcome["result"], outcome["action_taken"],
                metrics=outcome["metrics"], notes=outcome["notes"],
            )

    # Read-only council history shown in the demo UI.
    seed_dir = Path(seed_dir)
    seed_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(dataset.council_sessions, key=lambda s: s["created_at"])
    (seed_dir / "council_sessions.json").write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")

    return {**dataset.counts, "queued_actions": queued, "tracked_recommendations": recommendations}


# ---------------------------------------------------------------------------
# High-level operations
# ---------------------------------------------------------------------------

def _legacy_company() -> dict[str, Any]:
    from core.memory import load_memory

    return dict(load_memory().get("company") or {})


def _has_seeded_data(engine, org_id: int) -> bool:
    from core.db.models.clinic import Patient

    with Session(engine) as session:
        return session.query(Patient).filter(Patient.organization_id == org_id).count() > 0


def seed_demo(engine, *, anchor: date | None = None, seed_dir: Path = DEMO_SEED_DIR) -> dict[str, Any]:
    """Create the demo tenant and its data if it is not already seeded.
    Idempotent: a second call changes nothing. Refuses a database that
    already holds a non-demo organization."""
    with Session(engine) as session:
        problems = database_problems(session, _legacy_company())
    if problems:
        raise DemoSeedError("refusing to seed this database: " + "; ".join(problems))

    with allow_demo_writes(), seeding_environment(engine, seed_dir=seed_dir):
        with Session(engine) as session:
            org, _, _ = ensure_demo_identity(session)
            session.commit()
            org_id = org.id
        if _has_seeded_data(engine, org_id):
            return {"seeded": False, "reason": "already seeded", "organization_id": org_id}
        counts = seed_demo_data(engine, org_id, build_dataset(anchor), seed_dir=seed_dir)
    return {"seeded": True, "organization_id": org_id, "counts": counts}


def reset_demo(engine, *, anchor: date | None = None, seed_dir: Path = DEMO_SEED_DIR) -> dict[str, Any]:
    """Restore the demo tenant to its original seeded state. Operates ONLY
    on the demo tenant, and refuses outright — before writing anything — if
    the database holds any non-demo organization or the legacy company
    profile is not the demo's."""
    with Session(engine) as session:
        problems = database_problems(session, _legacy_company())
    if problems:
        raise DemoSeedError("refusing to reset this database: " + "; ".join(problems))

    from core.memory import reset_company

    with allow_demo_writes(), seeding_environment(engine, seed_dir=seed_dir):
        with Session(engine) as session:
            org, _, _ = ensure_demo_identity(session)
            deleted = wipe_demo_tenant_rows(session, org)
            session.commit()
            org_id = org.id
        reset_company()  # the legacy memory_store: safe, the check above proved it is demo-only
        seed_dir = Path(seed_dir)
        seed_dir.mkdir(parents=True, exist_ok=True)
        (seed_dir / "learning_memory.json").unlink(missing_ok=True)
        counts = seed_demo_data(engine, org_id, build_dataset(anchor), seed_dir=seed_dir)
    return {"reset": True, "organization_id": org_id, "deleted": deleted, "counts": counts}
