# Jarvis Automation Roadmap

Build order for turning Jarvis's automations from "Jarvis can answer if asked"
into "Jarvis checks and acts on his own." Build and test each phase fully
before starting the next one — do not jump ahead.

## Decision already made (superseded)
This originally said the scheduler runs locally via Windows Task
Scheduler. That's no longer true — real paying clients need uptime
independent of the founder's PC, so the scheduler now runs on a timer via
GitHub Actions (`.github/workflows/scheduler-master.yml`,
`scheduler-client-1.yml`), one workflow per client deployment. See
`docs/NEW_CLIENT_ONBOARDING.md` for wiring up a new client's workflow.

## Known gap — resolved
This section used to say clinic data (patients, appointments, packages,
payments, therapists) lived in local JSON at `data/pilot/*.json`
regardless of `DATABASE_URL`, separately from `core/memory.py`'s
Postgres/SQLite backend. That migration happened — `clinic_data_service.py`
now reads and writes through `core/memory.py` like everything else, so
clinic data has the same durability as the rest of the app's storage.

A related, more recently discovered issue: `services/jarvis_context.py`
(what every automation's and every Jarvis chat answer's business
grounding is built from) had its own, separate read path that was never
updated when the above migration happened — it kept reading the same now-
nonexistent `data/pilot/*.json` files. Fixed 2026-08-09; clinic-derived
signals (renewals due, patient inactivity, capacity risk, etc.) were
silently always empty before that fix. Worth remembering if a future
migration changes where clinic data lives again: check every read path,
not just the obvious write path.

## Phase 0 — Scheduler foundation (build first, blocks every other phase)
Create `scheduler/run_scheduled_checks.py`:
- Plain Python, no Streamlit dependency — imports `core.memory` directly so
  it reads/writes the same database the app uses (SQLite or Postgres,
  whichever `DATABASE_URL` currently points at).
- Designed to be run on a timer via Windows Task Scheduler (hourly to
  start). Document the exact Task Scheduler setup steps once built.
- Each run executes a list of independent "check" functions. Structure
  this so adding a new automation later means adding one function, not
  touching a big dispatcher.
- For Tier 1 (owner-facing, internal) checks: write results directly as
  an alert/report the owner sees next time they open Jarvis.
- For anything patient-facing: create a prepared item in the existing
  Approval Queue (`services/integration_manager_v21.py`), never send
  directly, until Phase 2+ explicitly revisits which items are safe to
  auto-send without approval.
- Must be idempotent: track what's already been alerted on (per patient/
  item) so the same renewal-due patient doesn't get flagged every single
  run.
- Test against real Supabase data before considering this phase done.

## Phase 1 — Tier 1 automations (low risk, build right after Phase 0)
In priority order:
1. Low Booking Alert
2. Capacity Alert (detection + surfacing, not auto-fixing)
3. Revenue Monitoring
4. Monthly Business Review
5. Lead Qualification / scoring
6. Appointment Reminder (24hr / 2hr)
7. Waiting List Automation

Blocked for now: Expense Monitoring — no expense data entity exists yet in
`services/clinic_data_service.py`. Needs a data model decision before this
can be built; flag to the founder rather than inventing a schema alone.

Note on item 7, Waiting List Automation: same situation as Expense
Monitoring — there's no `waiting_list` entity in
`services/clinic_data_service.py`, and nothing records who's waiting for
what. Built anyway, but scoped down to what doesn't require inventing
that entity: `scheduler.run_scheduled_checks.waiting_list_automation`
detects a future appointment that got Cancelled and tells the owner a
slot opened up — it does not maintain an actual waiting list or match a
specific patient to the slot. A real waiting list (patients register
interest, Jarvis offers them the slot automatically) needs the same kind
of data model decision as Expense Monitoring before it can be built.

Note on item 5, Lead Qualification: `data/pilot/leads.json` has no
established schema (currently empty; only a generic `status` field is
referenced anywhere else in the app). `lead_qualification_alert` is
written defensively against that — treats a lead as open unless status
looks terminal, checks a few plausible date-field names for staleness.
Revisit once real lead data exists and a schema is actually decided.

## Phase 2 — Tier 2 automations (patient-facing, tone-sensitive) — DONE
Built 2026-08-09, ahead of the "couple of weeks of Phase 1 running
reliably" gate this section originally called for — an explicit founder
decision to proceed anyway, not an oversight. All seven are implemented
in `scheduler/run_scheduled_checks.py` and approval-gated (queue into the
Approval Queue via `queue_patient_action`, never auto-send — the Phase 1
booking-confirmation/24hr-reminder auto-send exception was a one-time
decision for that content only and does not extend here):

1. **Birthday Automation** — added an optional `date_of_birth` field to
   the patient schema (`services/clinic_data_service.py`,
   `ui/patient_crm.py`); `birthday_automation()` matches month+day, once
   per patient per year.
2. **Google Review Automation** — no real Google API integration built or
   needed; added a `google_review_link` field to the company profile
   (Data Hub), `google_review_automation()` messages a patient 1-3 days
   after a completed appointment, and is a deliberate no-op if the link
   isn't configured.
3. **Missed Appointment Recovery** — `missed_appointment_recovery()`
   triggers on the existing `No-show` appointment status, not on a
   Scheduled appointment whose date merely passed (that's usually just an
   unupdated record, not a real miss).
4. **Inactive Patient Recovery** — `inactive_patient_recovery()` reuses
   `services/live_workflow_service.due_followups()`'s existing risk-flag
   logic rather than re-deriving it; re-fires monthly per patient while
   they remain inactive (not more often — a daily "we miss you" would be
   pestering, not a check-in).
5. **New Patient Recovery** — the roadmap didn't define this precisely;
   built as "a patient whose only completed visit was their first, with
   nothing else scheduled, 14+ days out" — the clearest signal available
   in existing data without leaning on the shakier `leads` schema. Revisit
   if a different definition was actually meant.
6. **Corporate Lead Automation** — there was no way to record a corporate
   lead anywhere in the app before this; added a minimal Corporate Leads
   CRM page (`ui/patient_crm.py`, new `corporate_clients` validation) so
   the automation has real data to work from. True to "research + draft
   only, never auto-send": `corporate_lead_automation()` prepares a Gmail
   *draft* (`provider="gmail", action="create_draft"`), which only ever
   creates a draft in Gmail for the owner to review, edit and send
   themselves — there's no path from this check to an email leaving on
   its own.
7. **Therapist Schedule Optimizer** — Tier 1 in practice (owner-facing
   suggestion, not patient-facing), since "suggest only, never auto-move
   patients" means nothing ever reaches a patient. Complements the
   existing `capacity_alert` (which only flags an over-booked therapist in
   isolation) by pairing that signal with whichever active therapist has
   spare capacity in the same window, so the owner gets a concrete
   rebalancing suggestion, not just a flag with no obvious next step.

Every item has a regression test under `tests/` (see README's list of
script-style scheduler tests), verified against local SQLite only.

## Phase 3 — Tier 3 (money/discount-adjacent) — stays approval-gated
Package Renewal, Payment Reminder, Membership Renewal, Referral Rewards.
Build the detection/drafting logic, but do not build a path to remove
the approval step for these without an explicit founder decision — these
involve real money and relationship risk if wrong.

## Phase 4 — Tier 4 (clinical/health) — permanently approval-gated
Recovery Prediction, Treatment Protocol Suggestions, ROM tracking
interpretation, Pain Score interpretation, Session Outcome Analysis,
Chronic Patient Monitoring, Body Composition Reports, Diet Reminder.
Do not build toward full autonomy here even in later phases — this is a
liability line, not a convenience one. A therapist should always be the
one who sees and confirms anything clinical before a patient does.

## Fitness Centre automations — separate future track
None of the current data model (patients/appointments/packages/
therapists) supports membership, attendance, diet, body composition, or
challenge-tracking concepts. Treat this as a distinct future scope, not
something to bolt onto the physiotherapy schema without a real design
pass.
