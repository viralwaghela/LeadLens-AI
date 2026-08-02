# Jarvis Automation Roadmap

Build order for turning Jarvis's automations from "Jarvis can answer if asked"
into "Jarvis checks and acts on his own." Build and test each phase fully
before starting the next one — do not jump ahead.

## Decision already made
Scheduler runs locally via Windows Task Scheduler for now (free, matches
current hosting). Revisit only once there are paying customers who need
uptime independent of the founder's own PC being on.

## Known gap — clinic data isn't in Postgres yet
`core/memory.py` (company profile, tasks, approvals, decisions, reports)
already moves to Postgres/Supabase whenever `DATABASE_URL` is set. But the
actual CRM data every automation needs to read — patients, appointments,
packages, payments, therapists — lives in `services/clinic_data_service.py`,
which is local JSON at `data/pilot/*.json` regardless of `DATABASE_URL`.
It does not move with the rest of the app's storage.

This is fine for now (single founder, single machine, matches the
"scheduler runs locally" decision above), but it means:
- Automations built in this roadmap only get Postgres durability for the
  alerts/approvals they produce, not for the underlying clinic data they
  read.
- This must be closed — either by moving `clinic_data_service` onto the
  same `DATABASE_URL`-aware backend as `core/memory.py`, or a deliberate
  replacement — before any real deployment off the founder's own PC, and
  before onboarding a second clinic (see CLAUDE.md's multi-tenancy gap;
  these are related but not the same problem — this one blocks even a
  single clinic's data from surviving a redeploy).

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

## Phase 2 — Tier 2 automations (patient-facing, tone-sensitive)
Only start once Phase 1 has been running reliably for at least a
couple of weeks with no bad surprises. In priority order:
1. Birthday Automation (needs a birthday/date_of_birth field added to
   the patient schema first — doesn't exist yet)
2. Google Review Automation (needs a new integration — none exists yet)
3. Missed Appointment Recovery
4. Inactive Patient Recovery (closest to already built —
   `services/live_workflow_service.due_followups()` already computes this)
5. New Patient Recovery
6. Corporate Lead Automation (research + draft only, never auto-send)
7. Therapist Schedule Optimizer (suggest only, never auto-move patients)

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
