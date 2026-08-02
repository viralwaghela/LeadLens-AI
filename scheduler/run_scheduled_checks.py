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

Example of the shape a real check will have (Phase 1 will add these —
none are registered yet, this file is the foundation only):

    @check
    def low_booking_alert() -> CheckResult:
        from services.clinic_data_service import clinic_metrics
        metrics = clinic_metrics()
        if metrics["upcoming_appointments"] < THRESHOLD:
            raised = raise_owner_alert(
                "low_booking_alert",
                date.today().isoformat(),
                title="Bookings are running low",
                message=f"Only {metrics['upcoming_appointments']} upcoming appointments.",
            )
            return CheckResult(alerts_raised=1 if raised else 0)
        return CheckResult()
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
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
