"""Jarvis's scheduler foundation (Automation Roadmap Phase 0).

Plain Python, no Streamlit dependency. Meant to be triggered on a timer by
Windows Task Scheduler (hourly to start) rather than run inside the app
process — see docs/SCHEDULER_SETUP.md for the exact setup steps.

Imports core.memory directly, so every check reads/writes the exact same
database the running app uses: SQLite locally, or Postgres/Supabase once
DATABASE_URL is set. (Note: the CRM's own patient/appointment/package
records live separately, in services.clinic_data_service, which is local
JSON regardless of DATABASE_URL — checks that need clinic data read from
there; only the alerts/approvals a check produces are backend-aware.)

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

from core.memory import add_memory_entry, ensure_database, get_memory_section  # noqa: E402

logger = logging.getLogger("scheduler")

RUN_LOG_SECTION = "scheduler_runs"
ALERT_LEDGER_SECTION = "scheduler_alert_ledger"


@dataclass
class CheckResult:
    """What one check function did, for the run log."""

    alerts_raised: int = 0
    approvals_queued: int = 0
    skipped_duplicate: int = 0
    detail: str = ""


CHECKS: list[Callable[[], "CheckResult | None"]] = []


def check(func: Callable[[], "CheckResult | None"]) -> Callable[[], "CheckResult | None"]:
    """Decorator: register a function to run on every scheduler pass."""
    CHECKS.append(func)
    return func


# ---------------------------------------------------------------------------
# Idempotency ledger, shared by both action helpers below so a check never
# has to build its own "have I already flagged this" bookkeeping.
# ---------------------------------------------------------------------------


def _ledger_key(check_name: str, item_key: str) -> str:
    return f"{check_name}::{item_key}"


def already_flagged(check_name: str, item_key: str) -> bool:
    """True if this exact (check, item) pair has already been alerted on
    or queued by a previous run. raise_owner_alert / queue_patient_action
    already call this internally; only check functions that need to
    branch on it directly (e.g. to skip other work too) need to call it
    themselves."""
    target = _ledger_key(check_name, item_key)
    return any(
        entry.get("data", {}).get("key") == target
        for entry in get_memory_section(ALERT_LEDGER_SECTION)
    )


def _mark_flagged(check_name: str, item_key: str, note: str = "") -> None:
    add_memory_entry(ALERT_LEDGER_SECTION, {
        "key": _ledger_key(check_name, item_key),
        "check": check_name,
        "item_key": item_key,
        "note": note,
    })


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
) -> bool:
    """Write a Tier 1, owner-facing alert Jarvis will surface next time
    the owner opens the app. Nothing external is contacted.

    `item_key` scopes idempotency — use something stable per real-world
    thing being flagged, e.g. f"{patient_id}:{date.today()}" for a daily
    per-patient check. Returns False (writing nothing) if this exact
    (check_name, item_key) pair was already alerted on by a previous run.
    """
    if already_flagged(check_name, item_key):
        return False
    add_memory_entry("reports", {
        "type": level,
        "title": title,
        "message": message,
        "department": department,
        "source": "scheduler",
        "check": check_name,
    })
    _mark_flagged(check_name, item_key, title)
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
) -> dict | None:
    """Prepare a patient-facing action in the existing Approval Queue.
    Never sends anything — the item sits as "Awaiting approval" until a
    human approves and executes it from the Action Center UI.

    Idempotent the same way as raise_owner_alert. integration_manager_v21
    also fingerprints the exact payload as a second, independent safety
    net, so even a ledger miss can't produce a duplicate for identical
    content. Returns None if this pair was already queued by a previous
    run.
    """
    if already_flagged(check_name, item_key):
        return None
    from services.integration_manager_v21 import prepare_execution

    item = prepare_execution(
        provider,
        action,
        payload,
        title=title,
        impact=impact,
        recommendation_id=f"scheduler:{check_name}",
    )
    _mark_flagged(check_name, item_key, title)
    return item


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
def low_booking_alert() -> CheckResult:
    """Tier 1: alert the owner when the clinic is under-booked for the
    week ahead relative to therapist capacity — catching it while there's
    still time to fill the gap, not after the week has already passed.

    Fires at most once per calendar day while the shortfall persists (a
    fresh item_key each day means it starts alerting again the next day
    if the situation hasn't improved, rather than alerting only once
    ever)."""
    from datetime import date, timedelta

    from services.clinic_data_service import list_records

    today = date.today()
    window = {
        (today + timedelta(days=offset)).isoformat()
        for offset in range(LOW_BOOKING_LOOKAHEAD_DAYS)
    }

    booked = sum(
        1
        for row in list_records("appointments")
        if row.get("status") == "Scheduled" and row.get("appointment_date") in window
    )
    weekly_capacity = sum(
        int(row.get("weekly_capacity", 0) or 0)
        for row in list_records("therapists")
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
    )
    return CheckResult(
        alerts_raised=1 if raised else 0,
        skipped_duplicate=0 if raised else 1,
        detail=detail,
    )


CAPACITY_ALERT_LOOKAHEAD_DAYS = 7


@check
def capacity_alert() -> CheckResult:
    """Tier 1: detection + surfacing only, never auto-fixing (per
    docs/AUTOMATION_ROADMAP.md) — flags a therapist whose scheduled load
    for the week ahead exceeds their own weekly_capacity, so the owner
    decides how to rebalance (reschedule, bring in cover, etc.) rather
    than Jarvis silently moving patients around.

    Fires at most once per calendar day per over-booked therapist while
    the situation persists."""
    from datetime import date, timedelta

    from services.clinic_data_service import list_records

    today = date.today()
    window = {
        (today + timedelta(days=offset)).isoformat()
        for offset in range(CAPACITY_ALERT_LOOKAHEAD_DAYS)
    }

    booked_by_therapist: dict[str, int] = {}
    for row in list_records("appointments"):
        if row.get("status") != "Scheduled" or row.get("appointment_date") not in window:
            continue
        therapist_id = row.get("therapist_id")
        if therapist_id:
            booked_by_therapist[therapist_id] = booked_by_therapist.get(therapist_id, 0) + 1

    therapist_names = {
        row.get("therapist_id"): row.get("name") or row.get("therapist_id")
        for row in list_records("therapists")
    }

    alerts_raised = 0
    skipped_duplicate = 0
    over_capacity_count = 0
    for row in list_records("therapists"):
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
def revenue_monitoring() -> CheckResult:
    """Tier 1: compares this month's revenue pace (Paid payments so far)
    against last month's revenue at the same day-of-month checkpoint, so
    a slow month gets caught while there's still time to act, not only
    after it's over.

    Fires at most once per calendar day while the shortfall persists."""
    import calendar
    from datetime import date

    from services.clinic_data_service import list_records

    today = date.today()
    this_month_start = today.replace(day=1)
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)
    last_month_days = calendar.monthrange(last_month_start.year, last_month_start.month)[1]
    checkpoint_day = min(today.day, last_month_days)
    last_month_checkpoint = last_month_start.replace(day=checkpoint_day)

    payments = list_records("payments")

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
    )
    return CheckResult(
        alerts_raised=1 if raised else 0,
        skipped_duplicate=0 if raised else 1,
        detail=detail,
    )


@check
def monthly_business_review() -> CheckResult:
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
    metrics = clinic_metrics()

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
def lead_qualification_alert() -> CheckResult:
    """Tier 1: surfaces leads that look like they need review or
    follow-up — never scores or contacts a lead automatically, just
    flags what the owner should look at.

    No lead schema is established yet in this codebase
    (data/pilot/leads.json is currently empty; the only field referenced
    anywhere else, services/jarvis_context.py, is a generic "status").
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
    leads = list_records("leads")

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
def appointment_reminder() -> CheckResult:
    """Tier 2+ (patient-facing): queues a WhatsApp reminder for patients
    with a Scheduled appointment coming up in ~24 hours or ~2 hours.
    Never sends anything itself — only drops a prepared item into the
    Approval Queue, same as every patient-facing check in this file; a
    human still has to approve and execute it from the Action Center UI.

    appointment_time isn't validated or format-enforced anywhere in
    services.clinic_data_service (it's free text), so this assumes the
    common "HH:MM" shape. An appointment whose time doesn't parse that
    way is skipped rather than guessed at — a reminder with the wrong
    time would be worse than no reminder.

    Each appointment gets at most one 24hr reminder queued and one 2hr
    reminder queued, ever — not per-day. These are one-time reminders
    tied to a specific appointment, not an ongoing situation like the
    other checks in this file."""
    from datetime import datetime

    from services.clinic_data_service import get_record, list_records

    now = datetime.now()
    queued_count = 0
    skipped_duplicate = 0
    skipped_unparseable = 0

    appointments = [
        row for row in list_records("appointments")
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

            patient = get_record("patients", appointment.get("patient_id"))
            if not patient or not bool(patient.get("consent_to_contact", False)):
                continue
            phone = str(patient.get("phone", "")).strip()
            if not phone:
                continue

            item_key = f"{appointment.get('appointment_id')}:{label}"
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
            )
            if item is not None:
                queued_count += 1
            else:
                skipped_duplicate += 1

    detail = (
        f"{queued_count} reminder(s) queued, {skipped_duplicate} already queued, "
        f"{skipped_unparseable} appointment(s) with unparseable time"
    )
    return CheckResult(
        approvals_queued=queued_count,
        skipped_duplicate=skipped_duplicate,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all_checks() -> dict[str, CheckResult]:
    results: dict[str, CheckResult] = {}
    for func in CHECKS:
        name = func.__name__
        try:
            outcome = func() or CheckResult()
        except Exception as error:  # a broken check must never take down the others
            logger.exception("Check %r raised an exception", name)
            outcome = CheckResult(detail=f"FAILED: {type(error).__name__}: {error}")
        results[name] = outcome
    return results


def _log_run(results: dict[str, CheckResult]) -> None:
    failures = {name: r.detail for name, r in results.items() if r.detail.startswith("FAILED")}
    add_memory_entry(RUN_LOG_SECTION, {
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "checks_run": len(results),
        "checks_failed": len(failures),
        "alerts_raised": sum(r.alerts_raised for r in results.values()),
        "approvals_queued": sum(r.approvals_queued for r in results.values()),
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
            "%-32s alerts=%d approvals=%d skipped=%d %s",
            name, result.alerts_raised, result.approvals_queued,
            result.skipped_duplicate, result.detail,
        )
    return 1 if any(r.detail.startswith("FAILED") for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
