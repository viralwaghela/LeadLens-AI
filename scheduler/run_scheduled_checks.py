"""Jarvis's scheduler foundation (Automation Roadmap Phase 0).

Plain Python, no Streamlit dependency. Originally meant to be triggered on
a timer by Windows Task Scheduler — see docs/SCHEDULER_SETUP.md for that
setup, and see the GitHub Actions workflow (.github/workflows/scheduler.yml)
for how it actually runs against a cloud deployment, where relying on the
founder's own PC being on isn't viable once a real client depends on it.

Imports core.memory directly, so every check reads/writes the exact same
database the running app uses: SQLite locally, or Postgres/Supabase once
DATABASE_URL is set. The CRM's own patient/appointment/package records
(services.clinic_data_service) now live in that same store too — they
used to be local JSON regardless of DATABASE_URL, fixed separately.

Each run executes every registered "check" function. Adding a new
automation later means writing one function and decorating it with
@check — nothing else in this file needs to change. A check takes no
arguments and returns a CheckResult (or None, treated as a no-op). A
check that raises only fails that one check; every other check still
runs, and the failure is recorded in the run log.

A check acts in one of two ways, per docs/AUTOMATION_ROADMAP.md:

- Tier 1 (owner-facing, internal): call raise_owner_alert(...). Writes
  straight into Jarvis's own alerts, visible next time the owner opens
  the app. Nothing external is ever contacted.
- Tier 2+ (patient-facing): call queue_patient_action(...). Never sends
  anything itself — it drops a prepared item into the existing Approval
  Queue (services.integration_manager_v21), which still requires a human
  to approve and execute it from the Action Center UI before anything
  goes out.

Both helpers take a stable `item_key` and are idempotent against it: the
same (check function, item_key) pair is only ever acted on once, so a
patient who is still renewal-due on the next hourly run doesn't get
flagged or queued again.

Automation Roadmap Phase 1, item 1 (low_booking_alert) is implemented
below — see it for the concrete shape a check takes in practice.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from core.identity.tenant_context import TenantContext  # noqa: E402
from core.memory import add_memory_entry, ensure_database, get_memory_section  # noqa: E402

logger = logging.getLogger("scheduler")

RUN_LOG_SECTION = "scheduler_runs"
ALERT_LEDGER_SECTION = "scheduler_alert_ledger"


@dataclass
class CheckResult:
    """What one check function did, for the run log."""

    alerts_raised: int = 0
    approvals_queued: int = 0
    messages_sent: int = 0
    skipped_duplicate: int = 0
    detail: str = ""


CHECKS: list[Callable[["TenantContext | None"], "CheckResult | None"]] = []


def check(
    func: Callable[["TenantContext | None"], "CheckResult | None"],
) -> Callable[["TenantContext | None"], "CheckResult | None"]:
    """Decorator: register a function to run on every scheduler pass.
    Phase 8.1: every check function now takes one optional keyword-only
    `context: TenantContext | None = None` argument — omitted (or None)
    preserves the exact pre-Phase-8.1 single-organization behavior
    (implicit, transitional-default-org resolution throughout); supplied,
    every CRM read and generated action inside the check is scoped to
    that context's organization_id instead. See run_all_checks()."""
    CHECKS.append(func)
    return func


def _org_id(context: "TenantContext | None") -> int | None:
    return context.organization_id if context is not None else None


# ---------------------------------------------------------------------------
# Idempotency ledger, shared by both action helpers below so a check never
# has to build its own "have I already flagged this" bookkeeping.
# ---------------------------------------------------------------------------


def _ledger_key(check_name: str, item_key: str, organization_id: int | None) -> str:
    """Phase 8.1: when an explicit organization_id is supplied (multi-org
    scheduler mode), the key is organization-scoped — so the exact same
    (check, item_key) pair firing under two different organizations is
    tracked independently, never suppressing one because the other
    already fired. Omitted (the single-organization legacy call shape):
    the key format is byte-identical to before Phase 8.1, so existing
    ledger entries for the transitional default organization keep
    matching without any migration."""
    if organization_id is None:
        return f"{check_name}::{item_key}"
    return f"org:{organization_id}::{check_name}::{item_key}"


def already_flagged(check_name: str, item_key: str, *, organization_id: int | None = None) -> bool:
    """True if this exact (check, item, organization) pair has already
    been alerted on or queued by a previous run. raise_owner_alert /
    queue_patient_action already call this internally; only check
    functions that need to branch on it directly (e.g. to skip other
    work too) need to call it themselves."""
    target = _ledger_key(check_name, item_key, organization_id)
    return any(
        entry.get("data", {}).get("key") == target
        for entry in get_memory_section(ALERT_LEDGER_SECTION)
    )


def _mark_flagged(check_name: str, item_key: str, note: str = "", *, organization_id: int | None = None) -> None:
    add_memory_entry(ALERT_LEDGER_SECTION, {
        "key": _ledger_key(check_name, item_key, organization_id),
        "check": check_name,
        "item_key": item_key,
        "note": note,
    })
    try:
        from services.tenant_operational_sync import sync_scheduler_alert_ledger_entry
        sync_scheduler_alert_ledger_entry(check_name, item_key, note)
    except Exception:  # noqa: BLE001 - Phase 5 shadow sync must never break the scheduler
        pass


# ---------------------------------------------------------------------------
# Action helpers checks call into
# ---------------------------------------------------------------------------


def raise_owner_alert(
    check_name: str,
    item_key: str,
    title: str,
    message: str,
    *,
    department: str = "Operations",
    level: str = "Risk",
    context: "TenantContext | None" = None,
) -> bool:
    """Write a Tier 1, owner-facing alert Jarvis will surface next time
    the owner opens the app. Nothing external is contacted.

    `item_key` scopes idempotency — use something stable per real-world
    thing being flagged, e.g. f"{patient_id}:{date.today()}" for a daily
    per-patient check. Returns False (writing nothing) if this exact
    (check_name, item_key) pair was already alerted on by a previous run.
    `context`, when supplied (Phase 8.1 multi-org scheduler mode), scopes
    idempotency to that organization — see _ledger_key().
    """
    organization_id = _org_id(context)
    if already_flagged(check_name, item_key, organization_id=organization_id):
        return False
    add_memory_entry("reports", {
        "type": level,
        "title": title,
        "message": message,
        "department": department,
        "source": "scheduler",
        "check": check_name,
    })
    _mark_flagged(check_name, item_key, title, organization_id=organization_id)
    return True


def queue_patient_action(
    check_name: str,
    item_key: str,
    *,
    provider: str,
    action: str,
    payload: dict,
    title: str,
    impact: str = "",
    context: "TenantContext | None" = None,
) -> dict | None:
    """Prepare a patient-facing action in the existing Approval Queue.
    Never sends anything — the item sits as "Awaiting approval" until a
    human approves and executes it from the Action Center UI.

    Idempotent the same way as raise_owner_alert. integration_manager_v21
    also fingerprints the exact payload as a second, independent safety
    net, so even a ledger miss can't produce a duplicate for identical
    content. Returns None if this pair was already queued by a previous
    run. `context`, when supplied, is passed straight through to
    prepare_execution() as its own tenant_context — the generated
    approval/queue item is stamped with that organization_id, never the
    transitional default, and idempotency is scoped to it too."""
    organization_id = _org_id(context)
    if already_flagged(check_name, item_key, organization_id=organization_id):
        return None
    from services.integration_manager_v21 import prepare_execution

    item = prepare_execution(
        provider,
        action,
        payload,
        title=title,
        impact=impact,
        recommendation_id=f"scheduler:{check_name}",
        tenant_context=context,
    )
    _mark_flagged(check_name, item_key, title, organization_id=organization_id)
    return item


def _company_profile(context: "TenantContext | None") -> dict:
    """Phase 8.1: org-scoped company/settings read for checks that need
    it (google_review_automation's review link,
    corporate_lead_automation's business name) — without this, those
    checks would read core.memory.load_company()'s single global dict
    regardless of which organization is being enumerated, leaking
    Organization A's business name/review link into Organization B's
    generated outreach. Falls back to the legacy global read when no
    context is supplied or org-scoped settings are off, exactly
    preserving pre-Phase-8.1 behavior."""
    from core.memory import load_company

    if context is None:
        return load_company()
    from services.platform_data import ORG_SCOPED_SETTINGS_ENABLED

    if not ORG_SCOPED_SETTINGS_ENABLED:
        return load_company()
    from core.db.session import session_scope
    from core.identity.organization_profile_service import get_settings
    from services.platform_data import _get_engine

    with session_scope(_get_engine()) as session:
        return get_settings(session, context.organization_id)


# ---------------------------------------------------------------------------
# Phase 1 checks (docs/AUTOMATION_ROADMAP.md)
# ---------------------------------------------------------------------------

# Tunable business judgment calls, kept as named constants rather than
# buried in the check body — adjust these directly, no other code changes
# needed. Current defaults: alert when the next 7 days' scheduled
# appointments cover less than half of active therapists' combined weekly
# capacity.
LOW_BOOKING_LOOKAHEAD_DAYS = 7
LOW_BOOKING_MIN_UTILIZATION = 0.5


@check
def low_booking_alert(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 1: alert the owner when the clinic is under-booked for the
    week ahead relative to therapist capacity — catching it while there's
    still time to fill the gap, not after the week has already passed.

    Fires at most once per calendar day while the shortfall persists (a
    fresh item_key each day means it starts alerting again the next day
    if the situation hasn't improved, rather than alerting only once
    ever)."""
    from datetime import date, timedelta

    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    today = date.today()
    window = {
        (today + timedelta(days=offset)).isoformat()
        for offset in range(LOW_BOOKING_LOOKAHEAD_DAYS)
    }

    booked = sum(
        1
        for row in list_records("appointments", organization_id=org_id)
        if row.get("status") == "Scheduled" and row.get("appointment_date") in window
    )
    weekly_capacity = sum(
        int(row.get("weekly_capacity", 0) or 0)
        for row in list_records("therapists", organization_id=org_id)
        if row.get("status") == "Active"
    )

    if weekly_capacity <= 0:
        return CheckResult(detail="No active therapist capacity on record; skipped.")

    utilization = booked / weekly_capacity
    detail = (
        f"{booked}/{weekly_capacity} slots booked for the next "
        f"{LOW_BOOKING_LOOKAHEAD_DAYS} days ({utilization:.0%})"
    )

    if utilization >= LOW_BOOKING_MIN_UTILIZATION:
        return CheckResult(detail=detail)

    raised = raise_owner_alert(
        "low_booking_alert",
        today.isoformat(),
        title="Bookings are running low for the week ahead",
        message=(
            f"Only {booked} of {weekly_capacity} weekly therapist slots are "
            f"booked for the next {LOW_BOOKING_LOOKAHEAD_DAYS} days "
            f"({utilization:.0%} utilization, target is "
            f"{LOW_BOOKING_MIN_UTILIZATION:.0%}+)."
        ),
        department="Operations",
        context=context,
    )
    return CheckResult(
        alerts_raised=1 if raised else 0,
        skipped_duplicate=0 if raised else 1,
        detail=detail,
    )


CAPACITY_ALERT_LOOKAHEAD_DAYS = 7


@check
def capacity_alert(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 1: detection + surfacing only, never auto-fixing (per
    docs/AUTOMATION_ROADMAP.md) — flags a therapist whose scheduled load
    for the week ahead exceeds their own weekly_capacity, so the owner
    decides how to rebalance (reschedule, bring in cover, etc.) rather
    than Jarvis silently moving patients around.

    Fires at most once per calendar day per over-booked therapist while
    the situation persists."""
    from datetime import date, timedelta

    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    today = date.today()
    window = {
        (today + timedelta(days=offset)).isoformat()
        for offset in range(CAPACITY_ALERT_LOOKAHEAD_DAYS)
    }

    booked_by_therapist: dict[str, int] = {}
    for row in list_records("appointments", organization_id=org_id):
        if row.get("status") != "Scheduled" or row.get("appointment_date") not in window:
            continue
        therapist_id = row.get("therapist_id")
        if therapist_id:
            booked_by_therapist[therapist_id] = booked_by_therapist.get(therapist_id, 0) + 1

    therapist_names = {
        row.get("therapist_id"): row.get("name") or row.get("therapist_id")
        for row in list_records("therapists", organization_id=org_id)
    }

    alerts_raised = 0
    skipped_duplicate = 0
    over_capacity_count = 0
    for row in list_records("therapists", organization_id=org_id):
        if row.get("status") != "Active":
            continue
        therapist_id = row.get("therapist_id")
        capacity = int(row.get("weekly_capacity", 0) or 0)
        if capacity <= 0:
            continue
        booked = booked_by_therapist.get(therapist_id, 0)
        if booked <= capacity:
            continue

        over_capacity_count += 1
        name = therapist_names.get(therapist_id, therapist_id)
        raised = raise_owner_alert(
            "capacity_alert",
            f"{therapist_id}:{today.isoformat()}",
            title=f"{name} is over capacity for the week ahead",
            message=(
                f"{name} has {booked} scheduled appointments over the next "
                f"{CAPACITY_ALERT_LOOKAHEAD_DAYS} days against a weekly "
                f"capacity of {capacity}."
            ),
            department="Operations",
            context=context,
        )
        if raised:
            alerts_raised += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{over_capacity_count} therapist(s) over capacity"
        if over_capacity_count
        else "no therapists over capacity"
    )
    return CheckResult(alerts_raised=alerts_raised, skipped_duplicate=skipped_duplicate, detail=detail)


REVENUE_MONITORING_MIN_PACE = 0.7


@check
def revenue_monitoring(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 1: compares this month's revenue pace (Paid payments so far)
    against last month's revenue at the same day-of-month checkpoint, so
    a slow month gets caught while there's still time to act, not only
    after it's over.

    Fires at most once per calendar day while the shortfall persists."""
    import calendar
    from datetime import date

    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    today = date.today()
    this_month_start = today.replace(day=1)
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)
    last_month_days = calendar.monthrange(last_month_start.year, last_month_start.month)[1]
    checkpoint_day = min(today.day, last_month_days)
    last_month_checkpoint = last_month_start.replace(day=checkpoint_day)

    payments = list_records("payments", organization_id=org_id)

    def _sum_paid(start: date, end_inclusive: date) -> float:
        total = 0.0
        for row in payments:
            if row.get("status") != "Paid":
                continue
            payment_date = row.get("payment_date")
            if payment_date and start.isoformat() <= payment_date <= end_inclusive.isoformat():
                total += float(row.get("amount", 0) or 0)
        return total

    this_month_total = _sum_paid(this_month_start, today)
    last_month_total_at_checkpoint = _sum_paid(last_month_start, last_month_checkpoint)

    if last_month_total_at_checkpoint <= 0:
        return CheckResult(
            detail=f"month-to-date {this_month_total:.0f}; no prior-month baseline to compare"
        )

    pace = this_month_total / last_month_total_at_checkpoint
    detail = (
        f"month-to-date {this_month_total:.0f} vs {last_month_total_at_checkpoint:.0f} "
        f"at the same point last month ({pace:.0%} of pace)"
    )

    if pace >= REVENUE_MONITORING_MIN_PACE:
        return CheckResult(detail=detail)

    raised = raise_owner_alert(
        "revenue_monitoring",
        today.isoformat(),
        title="Revenue is behind last month's pace",
        message=(
            f"₹{this_month_total:,.0f} collected so far this month vs "
            f"₹{last_month_total_at_checkpoint:,.0f} at the same point last "
            f"month ({pace:.0%} of last month's pace, target is "
            f"{REVENUE_MONITORING_MIN_PACE:.0%}+)."
        ),
        department="Finance",
        context=context,
    )
    return CheckResult(
        alerts_raised=1 if raised else 0,
        skipped_duplicate=0 if raised else 1,
        detail=detail,
    )


@check
def monthly_business_review(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 1: a periodic digest of clinic health for the owner — not an
    alert about anything being wrong. Fires once per calendar month
    (deduped by year-month), not every run.

    Uses type="Info" rather than "Risk" since this is a routine summary.
    Note: only reports with type="Risk" currently surface anywhere in the
    UI (the Mission Control alert banner — see ui/jarvis_mode.py's
    get_jarvis_alerts(), which only counts type=="Risk"). An "Info"
    report like this one is durably recorded but not yet visible in the
    UI. That's a real, pre-existing gap (the same one that leaves
    core/notifications.py's "Notification" type and
    ui/notification_center.py disconnected) — flagging it rather than
    mislabeling this as a "Risk" just to force visibility."""
    from datetime import date

    from services.clinic_data_service import clinic_metrics

    today = date.today()
    metrics = clinic_metrics(organization_id=_org_id(context))

    message = (
        f"Active patients: {metrics['active_patients']}/{metrics['patients']}. "
        f"Upcoming appointments: {metrics['upcoming_appointments']}. "
        f"Package renewals due: {metrics['renewals_due']}. "
        f"Revenue collected: ₹{metrics['payments_total']:,.0f}. "
        f"Pending payments: {metrics['pending_payments']}. "
        f"Active therapists: {metrics['therapists']}. "
        f"At-risk patients: {metrics['at_risk_patients']}."
    )

    raised = raise_owner_alert(
        "monthly_business_review",
        today.strftime("%Y-%m"),
        title=f"Monthly business review — {today.strftime('%B %Y')}",
        message=message,
        department="Executive",
        level="Info",
        context=context,
    )
    return CheckResult(
        alerts_raised=1 if raised else 0,
        skipped_duplicate=0 if raised else 1,
        detail=message,
    )


LEAD_STALE_DAYS = 3
LEAD_CLOSED_STATUSES = {"converted", "won", "lost", "declined", "closed", "archived"}
LEAD_DATE_FIELDS = ("created_at", "inquiry_date", "date", "created", "timestamp")


@check
def lead_qualification_alert(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 1: surfaces leads that look like they need review or
    follow-up — never scores or contacts a lead automatically, just
    flags what the owner should look at.

    No lead schema is established yet in this codebase (the "leads"
    clinic entity is currently empty; the only field referenced anywhere
    else, services/jarvis_context.py, is a generic "status").
    This check is written defensively against that uncertainty: it
    treats a lead as "open" unless its status matches a small set of
    terminal-looking values (LEAD_CLOSED_STATUSES), and looks for a
    creation/inquiry date under a few plausible field names
    (LEAD_DATE_FIELDS) to flag ones that have gone stale — if none of
    those fields are present on a lead, it's still counted as open but
    not scored for staleness. Revisit once a real lead schema exists.

    Fires at most once per calendar day while open/stale leads exist."""
    from datetime import date, datetime

    from services.clinic_data_service import list_records

    today = date.today()
    leads = list_records("leads", organization_id=_org_id(context))

    open_leads = [
        row for row in leads
        if str(row.get("status", "")).strip().casefold() not in LEAD_CLOSED_STATUSES
    ]

    if not open_leads:
        return CheckResult(detail="no open leads")

    missing_contact = [
        row for row in open_leads
        if not (row.get("phone") or row.get("email") or row.get("contact"))
    ]

    stale = []
    for row in open_leads:
        raw_date = next((row.get(field) for field in LEAD_DATE_FIELDS if row.get(field)), None)
        if not raw_date:
            continue
        try:
            lead_date = datetime.fromisoformat(str(raw_date)[:10]).date()
        except ValueError:
            continue
        if (today - lead_date).days >= LEAD_STALE_DAYS:
            stale.append(row)

    detail = (
        f"{len(open_leads)} open lead(s), {len(stale)} stale "
        f"({LEAD_STALE_DAYS}+ days), {len(missing_contact)} missing contact info"
    )

    if not stale and not missing_contact:
        return CheckResult(detail=detail)

    message_parts = []
    if stale:
        message_parts.append(
            f"{len(stale)} open lead(s) haven't been followed up in {LEAD_STALE_DAYS}+ days."
        )
    if missing_contact:
        message_parts.append(
            f"{len(missing_contact)} open lead(s) have no phone or email on record."
        )

    raised = raise_owner_alert(
        "lead_qualification_alert",
        today.isoformat(),
        title=f"{len(open_leads)} lead(s) need review",
        message=" ".join(message_parts),
        department="Sales",
        context=context,
    )
    return CheckResult(
        alerts_raised=1 if raised else 0,
        skipped_duplicate=0 if raised else 1,
        detail=detail,
    )


APPOINTMENT_REMINDER_WINDOWS = {
    "24hr": timedelta(hours=24),
    "2hr": timedelta(hours=2),
}
APPOINTMENT_REMINDER_TOLERANCE = timedelta(hours=1)


@check
def appointment_reminder(context: "TenantContext | None" = None) -> CheckResult:
    """Reminds patients with a Scheduled appointment coming up in ~24 hours
    or ~2 hours.

    The 24hr reminder is auto-sent directly as an RSVP-style message (see
    services.appointment_messaging.send_appointment_rsvp_reminder) — a
    founder-approved exception to the default approval-gated flow, same
    reasoning as the instant booking confirmation: low-risk transactional
    content, not money or clinical. The 2hr reminder is unchanged from
    before: it only drops a prepared item into the Approval Queue: a
    human still has to approve and execute it from the Action Center UI.

    appointment_time isn't validated or format-enforced anywhere in
    services.clinic_data_service (it's free text), so this assumes the
    common "HH:MM" shape. An appointment whose time doesn't parse that
    way is skipped rather than guessed at — a reminder with the wrong
    time would be worse than no reminder.

    Each appointment gets at most one 24hr reminder sent and one 2hr
    reminder queued, ever — not per-day. These are one-time reminders
    tied to a specific appointment, not an ongoing situation like the
    other checks in this file.

    Phase 8.1 known limitation: the 24hr branch's
    send_appointment_rsvp_reminder() call is NOT yet organization-context
    aware (services/appointment_messaging.py resolves its patient lookup,
    clinic name, and WhatsApp credentials implicitly, the same way this
    whole file did before Phase 8.1) — CRM reads for it below are
    correctly org-scoped, but the actual send still resolves credentials
    via the transitional default organization regardless of which
    organization is being enumerated. Tracked as remaining technical
    debt — see docs/V2_PHASE8_SAAS_ONBOARDING.md's Phase 8.1 addendum."""
    from datetime import datetime

    from services.appointment_messaging import send_appointment_rsvp_reminder
    from services.clinic_data_service import get_record, list_records

    org_id = _org_id(context)
    now = datetime.now()
    sent_count = 0
    queued_count = 0
    skipped_duplicate = 0
    skipped_unparseable = 0

    appointments = [
        row for row in list_records("appointments", organization_id=org_id)
        if row.get("status") == "Scheduled"
    ]

    for appointment in appointments:
        raw_date = str(appointment.get("appointment_date", "")).strip()
        raw_time = str(appointment.get("appointment_time", "")).strip()
        if not raw_date or not raw_time:
            continue
        try:
            appointment_dt = datetime.strptime(f"{raw_date} {raw_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            skipped_unparseable += 1
            continue

        time_until = appointment_dt - now
        if time_until.total_seconds() <= 0:
            continue

        for label, window in APPOINTMENT_REMINDER_WINDOWS.items():
            lower = window - APPOINTMENT_REMINDER_TOLERANCE
            upper = window + APPOINTMENT_REMINDER_TOLERANCE
            if not (lower <= time_until <= upper):
                continue

            item_key = f"{appointment.get('appointment_id')}:{label}"

            if label == "24hr":
                if already_flagged("appointment_reminder", item_key, organization_id=org_id):
                    skipped_duplicate += 1
                    continue
                result = send_appointment_rsvp_reminder(appointment)
                # None means no consent/phone on file — nothing to send,
                # and not worth tracking as "sent" or retrying every run,
                # so still mark it flagged either way.
                _mark_flagged("appointment_reminder", item_key, organization_id=org_id)
                if result is not None:
                    sent_count += 1
                continue

            patient = get_record("patients", appointment.get("patient_id"), organization_id=org_id)
            if not patient or not bool(patient.get("consent_to_contact", False)):
                continue
            phone = str(patient.get("phone", "")).strip()
            if not phone:
                continue

            body = (
                f"Hi {patient.get('name', 'there')}, this is a reminder for your "
                f"appointment on {raw_date} at {raw_time}"
                + (f" for {appointment.get('service')}" if appointment.get("service") else "")
                + ". Reply if you need to reschedule."
            )
            item = queue_patient_action(
                "appointment_reminder",
                item_key,
                provider="whatsapp",
                action="send_text",
                payload={"to": phone, "body": body},
                title=(
                    f"Appointment reminder ({label}) — "
                    f"{patient.get('name', appointment.get('patient_id'))}"
                ),
                impact="Routine appointment reminder, not a sales or money conversation.",
                context=context,
            )
            if item is not None:
                queued_count += 1
            else:
                skipped_duplicate += 1

    detail = (
        f"{sent_count} 24hr RSVP reminder(s) sent, {queued_count} 2hr reminder(s) queued, "
        f"{skipped_duplicate} already handled, "
        f"{skipped_unparseable} appointment(s) with unparseable time"
    )
    return CheckResult(
        approvals_queued=queued_count,
        messages_sent=sent_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


@check
def waiting_list_automation(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 1: surfaces a future appointment slot that just opened up via
    a cancellation, so the owner can offer it to a waitlisted patient.

    There is no waiting_list entity anywhere in
    services.clinic_data_service, and nothing records who's waiting for
    what — the roadmap's "Waiting List Automation" can't be built as
    literally "automatically match a waitlisted patient to an open slot"
    without a real data model decision first (same situation as Expense
    Monitoring, already blocked in docs/AUTOMATION_ROADMAP.md for
    exactly this reason — flagged there rather than inventing a schema
    here). This is a deliberately scoped-down piece of the idea that
    doesn't require inventing that entity: it detects a future Scheduled
    appointment that became Cancelled and tells the owner a slot opened
    up. Matching it to a specific patient is left to the owner — Jarvis
    doesn't know who's waiting, only that a slot appeared.

    Fires once per cancelled appointment, ever — not daily; a specific
    cancellation is a one-time event, not an ongoing situation."""
    from datetime import date

    from services.clinic_data_service import get_record, list_records

    org_id = _org_id(context)
    today = date.today().isoformat()
    cancelled_future = [
        row for row in list_records("appointments", organization_id=org_id)
        if row.get("status") == "Cancelled" and str(row.get("appointment_date", "")) >= today
    ]

    alerts_raised = 0
    skipped_duplicate = 0
    for appointment in cancelled_future:
        therapist = get_record("therapists", appointment.get("therapist_id"), organization_id=org_id)
        therapist_name = (
            therapist.get("name") if therapist else appointment.get("therapist_id", "an unknown therapist")
        )

        raised = raise_owner_alert(
            "waiting_list_automation",
            str(appointment.get("appointment_id")),
            title="A cancellation opened up an appointment slot",
            message=(
                f"{therapist_name} has an open slot on "
                f"{appointment.get('appointment_date')} at "
                f"{appointment.get('appointment_time') or 'an unspecified time'} "
                f"after a cancellation. Consider offering it to a waitlisted patient."
            ),
            department="Operations",
            context=context,
        )
        if raised:
            alerts_raised += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{alerts_raised} newly-opened slot(s) flagged, {skipped_duplicate} already flagged"
        if cancelled_future
        else "no cancelled future appointments"
    )
    return CheckResult(alerts_raised=alerts_raised, skipped_duplicate=skipped_duplicate, detail=detail)


# ---------------------------------------------------------------------------
# Phase 2 checks (docs/AUTOMATION_ROADMAP.md) — patient-facing, tone-
# sensitive. Every Phase 2 check uses queue_patient_action, never sends
# directly: a human always approves and executes from the Action Center UI.
# The Phase 1 24hr-reminder/booking-confirmation auto-send exception was a
# specific, one-time founder decision for that content only — it does not
# extend to anything built here.
# ---------------------------------------------------------------------------


@check
def birthday_automation(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 2: queues a birthday WhatsApp message for any consented patient
    whose recorded date_of_birth falls today (month and day match; the
    year is only used for age, never for matching).

    date_of_birth is an optional patient field (services.clinic_data_service)
    — a patient with nothing recorded is silently skipped, not flagged as
    missing data; birthdays are exactly the kind of thing clinics often
    don't collect at intake, and that's fine.

    Fires at most once per patient per calendar year (item_key includes
    the year), so re-running hourly through the day doesn't queue
    duplicates, and the same patient gets a fresh queued item next year."""
    from datetime import date

    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    today = date.today()
    queued_count = 0
    skipped_duplicate = 0
    skipped_no_contact = 0

    for patient in list_records("patients", organization_id=org_id):
        dob_text = str(patient.get("date_of_birth", "") or "").strip()
        if not dob_text:
            continue
        try:
            dob = date.fromisoformat(dob_text)
        except ValueError:
            continue
        if (dob.month, dob.day) != (today.month, today.day):
            continue
        if not bool(patient.get("consent_to_contact", False)):
            skipped_no_contact += 1
            continue
        phone = str(patient.get("phone", "")).strip()
        if not phone:
            skipped_no_contact += 1
            continue

        name = patient.get("name") or "there"
        item = queue_patient_action(
            "birthday_automation",
            f"{patient.get('patient_id')}:{today.year}",
            provider="whatsapp",
            action="send_text",
            payload={
                "to": phone,
                "body": (
                    f"Happy birthday, {name}! Wishing you health and "
                    "happiness from everyone at the clinic."
                ),
            },
            title=f"Birthday message — {name}",
            impact="Goodwill message, not a sales or money conversation.",
            context=context,
        )
        if item is not None:
            queued_count += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{queued_count} birthday message(s) queued, "
        f"{skipped_duplicate} already handled this year, "
        f"{skipped_no_contact} skipped (no consent or phone on file)"
    )
    return CheckResult(
        approvals_queued=queued_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


# Tunable: ask for a review once the visit has had a day or two to settle,
# but not so long after that the ask feels disconnected from the visit.
GOOGLE_REVIEW_MIN_DAYS_AFTER = 1
GOOGLE_REVIEW_MAX_DAYS_AFTER = 3


@check
def google_review_automation(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 2: queues a WhatsApp review request for a patient whose
    appointment was completed 1-3 days ago.

    No real Google API integration exists (or is needed) for this — it's
    a link to the clinic's own Google review page, set once in Data Hub
    (company.google_review_link). If that's not configured, this check
    is a deliberate no-op rather than sending a broken/blank link.

    Fires once per completed appointment, ever, not daily — a specific
    visit is a one-time event to ask about, same reasoning as
    waiting_list_automation's one-time cancellation flag."""
    from datetime import date

    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    review_link = str(_company_profile(context).get("google_review_link", "") or "").strip()
    if not review_link:
        return CheckResult(detail="no google_review_link configured in Data Hub; skipped")

    today = date.today()
    queued_count = 0
    skipped_duplicate = 0
    skipped_no_contact = 0

    completed = [
        row for row in list_records("appointments", organization_id=org_id)
        if row.get("status") == "Completed"
    ]
    for appointment in completed:
        raw_date = str(appointment.get("appointment_date", "")).strip()
        if not raw_date:
            continue
        try:
            appointment_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        days_since = (today - appointment_date).days
        if not (GOOGLE_REVIEW_MIN_DAYS_AFTER <= days_since <= GOOGLE_REVIEW_MAX_DAYS_AFTER):
            continue

        from services.clinic_data_service import get_record

        patient = get_record("patients", appointment.get("patient_id"), organization_id=org_id)
        if not patient or not bool(patient.get("consent_to_contact", False)):
            skipped_no_contact += 1
            continue
        phone = str(patient.get("phone", "")).strip()
        if not phone:
            skipped_no_contact += 1
            continue

        name = patient.get("name") or "there"
        item = queue_patient_action(
            "google_review_automation",
            str(appointment.get("appointment_id")),
            provider="whatsapp",
            action="send_text",
            payload={
                "to": phone,
                "body": (
                    f"Hi {name}, thank you for visiting us! If you have a "
                    f"moment, we'd really appreciate a review: {review_link}"
                ),
            },
            title=f"Review request — {name}",
            impact="Goodwill/reputation request, not a sales or money conversation.",
            context=context,
        )
        if item is not None:
            queued_count += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{queued_count} review request(s) queued, "
        f"{skipped_duplicate} already handled, "
        f"{skipped_no_contact} skipped (no consent or phone on file)"
    )
    return CheckResult(
        approvals_queued=queued_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


@check
def missed_appointment_recovery(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 2: queues a WhatsApp reschedule offer for a patient whose
    appointment is marked No-show.

    Trusts the existing No-show status (services.clinic_data_service.
    APPOINTMENT_STATUSES) rather than inferring a miss from a Scheduled
    appointment whose date has simply passed — the latter is usually just
    staff not having updated the record yet, not a real no-show, and
    guessing wrong would send a patient a "we missed you" message about a
    visit they actually attended.

    Fires once per appointment, ever, not daily — a specific missed visit
    is a one-time event to follow up on."""
    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    queued_count = 0
    skipped_duplicate = 0
    skipped_no_contact = 0

    no_shows = [
        row for row in list_records("appointments", organization_id=org_id)
        if row.get("status") == "No-show"
    ]
    for appointment in no_shows:
        from services.clinic_data_service import get_record

        patient = get_record("patients", appointment.get("patient_id"), organization_id=org_id)
        if not patient or not bool(patient.get("consent_to_contact", False)):
            skipped_no_contact += 1
            continue
        phone = str(patient.get("phone", "")).strip()
        if not phone:
            skipped_no_contact += 1
            continue

        name = patient.get("name") or "there"
        item = queue_patient_action(
            "missed_appointment_recovery",
            str(appointment.get("appointment_id")),
            provider="whatsapp",
            action="send_text",
            payload={
                "to": phone,
                "body": (
                    f"Hi {name}, we missed you at your last appointment "
                    "and wanted to check in. Would you like to reschedule?"
                ),
            },
            title=f"Missed appointment follow-up — {name}",
            impact="Routine reschedule offer, not a sales or money conversation.",
            context=context,
        )
        if item is not None:
            queued_count += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{queued_count} reschedule offer(s) queued, "
        f"{skipped_duplicate} already handled, "
        f"{skipped_no_contact} skipped (no consent or phone on file)"
    )
    return CheckResult(
        approvals_queued=queued_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


@check
def inactive_patient_recovery(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 2: queues a WhatsApp check-in for each patient
    services.live_workflow_service already identifies as inactive or
    overdue for a visit (reuses due_followups() rather than re-deriving
    the same risk logic — see that module for the actual flag rules).

    Being inactive is an ongoing situation, not a one-time event (unlike
    a missed appointment or a completed visit), so this re-fires once per
    patient per calendar month while they remain inactive, rather than
    only ever once — but not more often than that, since a monthly
    "we miss you" is reasonable and a daily one would just be pestering
    someone who already didn't respond."""
    from datetime import date

    from services.live_workflow_service import due_followups

    org_id = _org_id(context)
    month_key = date.today().isoformat()[:7]
    queued_count = 0
    skipped_duplicate = 0
    skipped_no_phone = 0

    for patient in due_followups("Inactive patient recovery", organization_id=org_id):
        phone = str(patient.get("phone", "")).strip()
        if not phone:
            skipped_no_phone += 1
            continue

        name = patient.get("name") or "there"
        item = queue_patient_action(
            "inactive_patient_recovery",
            f"{patient.get('patient_id')}:{month_key}",
            provider="whatsapp",
            action="send_text",
            payload={
                "to": phone,
                "body": (
                    f"Hi {name}, we haven't seen you in a while and wanted "
                    "to check in. Would you like help scheduling your next "
                    "session?"
                ),
            },
            title=f"Inactive patient check-in — {name}",
            impact="Routine relationship check-in, not a sales or money conversation.",
            context=context,
        )
        if item is not None:
            queued_count += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{queued_count} check-in(s) queued, "
        f"{skipped_duplicate} already handled this month, "
        f"{skipped_no_phone} skipped (no phone on file)"
    )
    return CheckResult(
        approvals_queued=queued_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


# The roadmap doesn't pin down exactly what "New Patient Recovery" means.
# Defined here as: a patient whose only completed visit was their first,
# with nothing else scheduled — the clearest, most grounded read of "new
# patient at risk of never coming back" using data that already exists,
# rather than the shakier `leads` schema (see lead_qualification_alert's
# own caveat about that). Revisit if the founder means something different.
NEW_PATIENT_FOLLOWUP_MIN_DAYS = 14


@check
def new_patient_recovery(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 2: queues a "book your next session" WhatsApp message for a
    patient whose only completed appointment was their first, with no
    future appointment scheduled and enough time having passed to assume
    they're not coming back on their own.

    Fires once per patient, ever — a specific first visit is a one-time
    thing to follow up on, not an ongoing situation."""
    from datetime import date

    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    today = date.today()
    queued_count = 0
    skipped_duplicate = 0
    skipped_no_contact = 0

    appointments = list_records("appointments", organization_id=org_id)
    by_patient: dict[str, list[dict]] = {}
    for row in appointments:
        by_patient.setdefault(str(row.get("patient_id")), []).append(row)

    for patient in list_records("patients", organization_id=org_id):
        patient_id = str(patient.get("patient_id"))
        patient_appointments = by_patient.get(patient_id, [])
        completed = [
            row for row in patient_appointments if row.get("status") == "Completed"
        ]
        if len(completed) != 1:
            continue
        has_future_scheduled = any(
            row.get("status") == "Scheduled"
            and str(row.get("appointment_date", "")) >= today.isoformat()
            for row in patient_appointments
        )
        if has_future_scheduled:
            continue

        raw_date = str(completed[0].get("appointment_date", "")).strip()
        try:
            visit_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if (today - visit_date).days < NEW_PATIENT_FOLLOWUP_MIN_DAYS:
            continue

        if not bool(patient.get("consent_to_contact", False)):
            skipped_no_contact += 1
            continue
        phone = str(patient.get("phone", "")).strip()
        if not phone:
            skipped_no_contact += 1
            continue

        name = patient.get("name") or "there"
        item = queue_patient_action(
            "new_patient_recovery",
            patient_id,
            provider="whatsapp",
            action="send_text",
            payload={
                "to": phone,
                "body": (
                    f"Hi {name}, it's been a little while since your first "
                    "visit with us. Would you like to book your next "
                    "session?"
                ),
            },
            title=f"New patient follow-up — {name}",
            impact="Routine booking follow-up, not a sales or money conversation.",
            context=context,
        )
        if item is not None:
            queued_count += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{queued_count} follow-up(s) queued, "
        f"{skipped_duplicate} already handled, "
        f"{skipped_no_contact} skipped (no consent or phone on file)"
    )
    return CheckResult(
        approvals_queued=queued_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


@check
def corporate_lead_automation(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 2, research + draft only, never auto-send — deliberately more
    cautious than the standard "queue a WhatsApp, human approves and
    executes" Tier 2 pattern. Instead of preparing a send-able action,
    this prepares a Gmail *draft* (provider="gmail", action="create_draft")
    for a new corporate lead. Executing that item only ever creates a
    draft in Gmail — see services.integration_manager_v21 — which the
    owner must still open, review, edit and send themselves. There is no
    path from this check to an email actually leaving the building.

    Requires services.clinic_data_service's corporate_clients entity (a
    lead with no email on file is skipped — there's nothing to draft to).

    Fires once per lead, ever, not daily — a specific new lead is a
    one-time thing to draft outreach for, and re-drafting the same lead
    every hour while it sits in "New" would just be noise."""
    from services.clinic_data_service import list_records

    org_id = _org_id(context)
    business_name = str(_company_profile(context).get("business_name", "") or "our clinic")
    queued_count = 0
    skipped_duplicate = 0
    skipped_no_email = 0

    new_leads = [
        row for row in list_records("corporate_clients", organization_id=org_id)
        if row.get("status") == "New"
    ]
    for lead in new_leads:
        email = str(lead.get("email", "")).strip()
        if not email:
            skipped_no_email += 1
            continue

        company_name = lead.get("company_name") or "there"
        contact_name = lead.get("contact_name") or ""
        greeting = f"Hi {contact_name}," if contact_name else "Hi,"
        item = queue_patient_action(
            "corporate_lead_automation",
            str(lead.get("client_id")),
            provider="gmail",
            action="create_draft",
            payload={
                "to": email,
                "subject": f"Corporate wellness partnership with {business_name}",
                "body": (
                    f"{greeting}\n\n"
                    f"We'd love to explore a corporate wellness partnership "
                    f"between {company_name} and {business_name}. Happy to "
                    "share details on packages and pricing for your team "
                    "whenever convenient — let us know a good time to talk.\n\n"
                    "Best regards"
                ),
            },
            title=f"Corporate outreach draft — {company_name}",
            impact="Draft only — creates a Gmail draft for the owner to review and send, nothing is sent automatically.",
            context=context,
        )
        if item is not None:
            queued_count += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{queued_count} outreach draft(s) prepared, "
        f"{skipped_duplicate} already handled, "
        f"{skipped_no_email} skipped (no email on file)"
    )
    return CheckResult(
        approvals_queued=queued_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


@check
def therapist_schedule_optimizer(context: "TenantContext | None" = None) -> CheckResult:
    """Tier 1: suggest only, never auto-move a patient between therapists
    (per docs/AUTOMATION_ROADMAP.md) — complements capacity_alert, which
    only flags an over-booked therapist in isolation. This pairs that
    signal with whichever active therapist has spare capacity in the same
    window, so the owner gets a concrete rebalancing suggestion instead
    of just "X is over capacity" with no obvious next step.

    Fires at most once per calendar day per (over-booked, under-booked)
    pair while the imbalance persists."""
    from datetime import date, timedelta

    from services.clinic_data_service import list_records

    today = date.today()
    org_id = _org_id(context)
    window = {
        (today + timedelta(days=offset)).isoformat()
        for offset in range(CAPACITY_ALERT_LOOKAHEAD_DAYS)
    }

    booked_by_therapist: dict[str, int] = {}
    for row in list_records("appointments", organization_id=org_id):
        if row.get("status") != "Scheduled" or row.get("appointment_date") not in window:
            continue
        therapist_id = row.get("therapist_id")
        if therapist_id:
            booked_by_therapist[therapist_id] = booked_by_therapist.get(therapist_id, 0) + 1

    active_therapists = [
        row for row in list_records("therapists", organization_id=org_id) if row.get("status") == "Active"
    ]

    over_booked = []
    spare_capacity = []
    for row in active_therapists:
        therapist_id = row.get("therapist_id")
        capacity = int(row.get("weekly_capacity", 0) or 0)
        if capacity <= 0:
            continue
        booked = booked_by_therapist.get(therapist_id, 0)
        spare = capacity - booked
        if spare < 0:
            over_booked.append((therapist_id, row.get("name") or therapist_id, -spare))
        elif spare > 0:
            spare_capacity.append((therapist_id, row.get("name") or therapist_id, spare))

    # Greedily pair the most over-booked therapist with whoever has the
    # most spare room, largest imbalance first — good enough for a
    # suggestion the owner will sanity-check anyway, not a scheduling
    # engine that needs to be provably optimal.
    over_booked.sort(key=lambda item: item[2], reverse=True)
    spare_capacity.sort(key=lambda item: item[2], reverse=True)

    alerts_raised = 0
    skipped_duplicate = 0
    suggestions_made = 0
    for over_id, over_name, overage in over_booked:
        candidate = next((c for c in spare_capacity if c[0] != over_id), None)
        if candidate is None:
            continue
        under_id, under_name, spare = candidate
        suggestions_made += 1

        move_count = min(overage, spare)
        raised = raise_owner_alert(
            "therapist_schedule_optimizer",
            f"{over_id}:{under_id}:{today.isoformat()}",
            title=f"Consider rebalancing {over_name}'s schedule",
            message=(
                f"{over_name} is booked {overage} appointment(s) over "
                f"capacity for the next {CAPACITY_ALERT_LOOKAHEAD_DAYS} days, "
                f"while {under_name} has {spare} appointment(s) of spare "
                f"capacity in the same window. Consider moving up to "
                f"{move_count} appointment(s) from {over_name} to "
                f"{under_name} — this is a suggestion only; no appointment "
                "has been changed."
            ),
            department="Operations",
            context=context,
        )
        if raised:
            alerts_raised += 1
        else:
            skipped_duplicate += 1

    detail = (
        f"{suggestions_made} rebalancing suggestion(s) surfaced"
        if suggestions_made
        else "no rebalancing opportunity found"
    )
    return CheckResult(alerts_raised=alerts_raised, skipped_duplicate=skipped_duplicate, detail=detail)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _multi_org_scheduler_enabled() -> bool:
    return os.getenv("LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED", "").strip().lower() in {
        "1", "true", "yes",
    }


def resolve_scheduler_organizations() -> list[int]:
    """The set of organizations this scheduler run should execute for.

    Phase 8: when LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED is on, this
    genuinely enumerates every ACTIVE organization that has explicitly
    opted into automations (OrganizationSettings.automations_enabled —
    defaults False for every organization, including a freshly
    provisioned one, so a new clinic never fires outbound automations
    before an operator deliberately turns it on). Off (the default):
    unchanged Phase 5 behavior — always exactly the single transitional
    organization every other Phase 2-4 component resolves, since that's
    genuinely all that's meant to run automatically in a deployment that
    hasn't opted into multi-org scheduling. Returns an empty list (fails
    closed, runs nothing) rather than guessing if resolution itself
    fails — see docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md and
    docs/V2_PHASE8_SAAS_ONBOARDING.md.

    NOTE (Phase 8 known limitation, documented rather than silently
    implied as solved): the 14 check functions CHECKS iterates below are
    still zero-argument and read/write CRM data via the same implicit,
    session-based organization resolution every other live caller uses
    (core.identity.live_organization) — they are not yet individually
    parameterized to run once per enumerated organization against THAT
    organization's own tenant-scoped data. This function correctly
    identifies WHICH organizations are eligible; looping the check
    functions' actual CRM/messaging logic per-organization is deferred,
    tracked technical debt (see docs/V2_PHASE8_SAAS_ONBOARDING.md's
    "technical debt" section) — do not assume multi-org scheduler
    ENUMERATION implies multi-org scheduler EXECUTION content yet."""
    try:
        from core.db.session import make_engine, session_scope

        engine = make_engine()
        with session_scope(engine) as session:
            if not _multi_org_scheduler_enabled():
                from core.identity.tenant_context import ActorType, build_transitional_context

                context = build_transitional_context(session, actor_type=ActorType.SCHEDULER)
                return [context.organization_id]

            from core.db.models.organization import Organization, OrganizationSettings, OrganizationStatus

            rows = (
                session.query(Organization.id)
                .join(OrganizationSettings, OrganizationSettings.organization_id == Organization.id)
                .filter(
                    Organization.status == OrganizationStatus.ACTIVE,
                    OrganizationSettings.automations_enabled.is_(True),
                )
                .order_by(Organization.id.asc())
                .all()
            )
            return [row[0] for row in rows]
    except Exception:  # noqa: BLE001 - resolution failure must not crash the whole scheduler run
        logger.error("scheduler_org_scope_failure: could not resolve any organization for this run.", exc_info=True)
        return []


def _run_one_check(func, context: "TenantContext | None") -> CheckResult:
    try:
        return func(context) or CheckResult()
    except Exception as error:  # a broken check must never take down the others
        logger.exception("Check %r raised an exception", func.__name__)
        return CheckResult(detail=f"FAILED: {type(error).__name__}: {error}")


def _merge_results(a: CheckResult, b: CheckResult) -> CheckResult:
    detail = a.detail
    if b.detail:
        detail = f"{detail} | {b.detail}" if detail else b.detail
    return CheckResult(
        alerts_raised=a.alerts_raised + b.alerts_raised,
        approvals_queued=a.approvals_queued + b.approvals_queued,
        messages_sent=a.messages_sent + b.messages_sent,
        skipped_duplicate=a.skipped_duplicate + b.skipped_duplicate,
        detail=detail,
    )


def run_all_checks() -> dict[str, CheckResult]:
    """Phase 8.1: when LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED is on,
    every enumerated organization gets its own TenantContext
    (actor_type=SCHEDULER, no fake human identity — see
    core.identity.tenant_context.build_system_context()), and every one
    of the 14 check functions runs once per organization, scoped to
    that context: reads, generated approvals/queue items, and
    idempotency-ledger keys are all organization-specific (see
    _org_id()/_ledger_key() and each check function's own
    organization_id= threading). Results are summed per check name
    across organizations so the run log's shape is unchanged.

    Off (the default): unchanged Phase 5 behavior — every check runs
    exactly once with context=None, which resolves everything via the
    same implicit, transitional-default-organization path every
    pre-Phase-8.1 call used. No organization-enumeration overhead, no
    behavior change, for any deployment that hasn't opted in."""
    organizations = resolve_scheduler_organizations()
    if not organizations:
        logger.warning("No organization resolved for this scheduler run — running checks against legacy store only.")

    if not _multi_org_scheduler_enabled():
        results: dict[str, CheckResult] = {}
        for func in CHECKS:
            results[func.__name__] = _run_one_check(func, None)
        return results

    from core.db.session import make_engine, session_scope
    from core.identity.tenant_context import ActorType, build_system_context

    results = {func.__name__: CheckResult() for func in CHECKS}
    # Not disposed here, deliberately — matches resolve_scheduler_organizations()'s
    # own engine handling immediately above and every module-level
    # `_get_engine()` cache elsewhere in the codebase (e.g.
    # services/relational_sync_service.py): make_engine() may return an
    # externally-owned/shared engine (as every test in this file's
    # multi-org fixtures does), and disposing an engine this function
    # doesn't own would tear down that shared connection pool out from
    # under the caller — for SQLite's in-memory test databases in
    # particular, disposing discards the only connection holding the
    # database alive, silently dropping every table.
    engine = make_engine()
    for organization_id in organizations:
        with session_scope(engine) as session:
            context = build_system_context(
                session, organization_id=organization_id, actor_type=ActorType.SCHEDULER,
                source="scheduler_multi_org",
            )
        for func in CHECKS:
            outcome = _run_one_check(func, context)
            results[func.__name__] = _merge_results(results[func.__name__], outcome)
    return results


def _log_run(results: dict[str, CheckResult]) -> None:
    failures = {name: r.detail for name, r in results.items() if r.detail.startswith("FAILED")}
    add_memory_entry(RUN_LOG_SECTION, {
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "checks_run": len(results),
        "checks_failed": len(failures),
        "alerts_raised": sum(r.alerts_raised for r in results.values()),
        "approvals_queued": sum(r.approvals_queued for r in results.values()),
        "messages_sent": sum(r.messages_sent for r in results.values()),
        "skipped_duplicate": sum(r.skipped_duplicate for r in results.values()),
        "failures": failures,
    })


def _backend_name() -> str:
    return "Postgres" if os.getenv("DATABASE_URL", "").strip() else "SQLite"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_database()
    logger.info("Running %d scheduled check(s) against %s backend", len(CHECKS), _backend_name())
    results = run_all_checks()
    _log_run(results)
    for name, result in results.items():
        logger.info(
            "%-32s alerts=%d approvals=%d sent=%d skipped=%d %s",
            name, result.alerts_raised, result.approvals_queued,
            result.messages_sent, result.skipped_duplicate, result.detail,
        )
    return 1 if any(r.detail.startswith("FAILED") for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
