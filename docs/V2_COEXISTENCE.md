# V2 Phase 0 — coexistence architecture

Read this before touching anything in `core/db/`, `alembic/`, or
`scripts/bootstrap_beyond_pain_org.py`. It exists so a future session
doesn't accidentally wire the new schema into a live read or write path
before that's actually the plan.

## The rule

```
LeadLens (live, production)
  → core/memory.py
    → memory_store.payload (one JSON blob, one row, id=1)
      → this is STILL the only thing any live code path reads or writes

V2 relational schema (Phase 0, dormant)
  → core/db/ (SQLAlchemy models, engine/session helpers)
    → alembic/ (migrations)
      → real Postgres/SQLite tables
        → nothing in app.py, dashboard.py, services/, ui/, or
          scheduler/ imports anything from core/db/ or alembic/
```

**`core/memory.py` remains the production source of truth until an
explicit later phase changes that.** Phase 0 does not change it, does
not remove it, does not read from it, does not write to it. It is
completely untouched — verify this yourself with `git diff` against
`core/memory.py` after any Phase 0 work; if that file shows a diff,
something went outside Phase 0's scope.

## Why the two stores can safely share one database

In production (`DATABASE_URL` set to real Postgres), Phase 0's tables
(`organizations`, `patients`, `appointments`, etc. — see
`core/db/models/`) live in the **same** Postgres database as
`memory_store`, as separate tables. This is deliberate, not an oversight
— the task that built Phase 0 was explicit: "use the existing
DATABASE_URL configuration where practical, do not create a second
production database." Nothing about adding new, empty, unused tables to
a database can affect `memory_store`'s own row — they don't reference
it, don't trigger off it, and Postgres has no mechanism by which
`CREATE TABLE organizations (...)` could alter unrelated existing data.

Locally (`DATABASE_URL` unset), Phase 0 uses a **separate** SQLite file:
`database/leadlens_v2.db`, distinct from `core/memory.py`'s own
`database/leadlens.db`. This is also deliberate — see
`core/db/session.py`'s module docstring for the reasoning (avoiding any
raw-`sqlite3`-vs-SQLAlchemy cross-access ambiguity while nothing is wired
together yet). Both files are gitignored.

## What exists and what it's for

| Path | What it is | Live-wired? |
|---|---|---|
| `core/db/base.py` | Shared SQLAlchemy declarative `Base` | No |
| `core/db/session.py` | Engine/session helpers, `DATABASE_URL` resolution | No |
| `core/db/models/organization.py` | `Organization`, `OrganizationSettings` | No |
| `core/db/models/identity.py` | `User`, `Membership` | No |
| `core/db/models/clinic.py` | `Patient`, `Appointment`, `Package`, `PackageTemplate`, `Payment`, `ProgressNote`, `Lead`, `CorporateClient`, `Therapist`, `Service` | No |
| `core/db/models/operations.py` | `SchedulerRun`, `SchedulerAlertLedgerEntry`, `Approval`, `ExecutionQueueItem`, `SecurityAuditEvent` | No |
| `core/db/models/jarvis.py` | `JarvisLearningRecord` (deliberately minimal placeholder — see that file's own docstring for why full normalization is deferred) | No |
| `alembic/` | Migration framework, one migration: `phase0 initial schema` | No — CLI-only, never imported by the app |
| `scripts/bootstrap_beyond_pain_org.py` | Explicit, idempotent, manually-run script to create Beyond Pain's future `Organization` row | No — never imported by the app, never run automatically |
| `.github/workflows/ci.yml` | Runs the full existing regression suite + Phase 0 tests on every PR/push | N/A — CI infrastructure, not application code |

## Field/schema mapping (why the relational schema looks the way it does)

Every field on every model in `core/db/models/clinic.py` and
`operations.py` was copied from the **actual current code** in
`services/clinic_data_service.py`'s `ENTITY_META` and
`_validate_record()`, and `services/integration_manager_v21.py`'s
`prepare_execution()`/`execute_item()` — not invented or guessed. Each
model's docstring says exactly which live file/section it mirrors.

`external_id` on every clinic/operational entity holds the current
string ID format (`"P-001"`, `"A-014"`, `"EXEC-9FD55BB760"`, etc.), org-
scoped-unique, specifically so a future backfill can preserve today's
IDs exactly rather than silently renumbering everything.

One genuinely new thing with no live counterpart: `Service` (a
service/class catalog) — `services/clinic_data_service.py` has no
"services" entity today; service names are free text on
Appointment/Package records. This was requested explicitly for Phase 0
as forward-looking infrastructure, documented as such in its own
docstring, not treated as a migration target for anything that exists.

## Cross-tenant foreign key design

Every child table that references another tenant-owned table
(`Appointment → Patient`, `Payment → Package`, `ExecutionQueueItem →
Approval`, etc.) uses a **composite** foreign key —
`(organization_id, patient_id) REFERENCES patients(organization_id, id)`
— not a bare `patient_id REFERENCES patients(id)`. This makes it
impossible at the database level for an Appointment row in one
organization to reference a Patient row in a different organization.
`tests/test_phase0_schema.py::test_composite_foreign_keys_enforce_same_organization`
proves this directly.

**Important SQLite-specific note, found and fixed during Phase 0's own
verification:** SQLite does not enforce foreign keys by default — a
bare `create_engine("sqlite:///...")` silently allows a cross-tenant
reference that Postgres would reject. `core/db/session.py`'s
`make_engine()` enables `PRAGMA foreign_keys=ON` on every SQLite
connection specifically so local/test behavior matches production
Postgres behavior. Always construct engines via `make_engine()`, not a
bare `create_engine()` call, or this protection silently doesn't apply.

## The future backfill path (not built yet)

When a later phase actually migrates Beyond Pain's live data onto this
schema, the intended sequence is:

```
old JSON (core/memory.py's memory_store.payload)
  → read via services.clinic_data_service.list_records() (already
    tenant-agnostic in shape — it returns plain dicts matching this
    schema's field names almost 1:1)
  → write into core/db/'s relational tables, preserving external_id
    exactly, under a real Organization row (bootstrap_beyond_pain_org.py
    creates that row; the backfill script itself does not exist yet)
  → verification: row-count + spot-check diff, per entity, between the
    old JSON list and the new table
  → dual-read (both stores kept in sync) if the cutover needs to happen
    gradually rather than atomically
  → read cutover, one entity at a time, smallest/lowest-risk first
    (leads and corporate_clients before patients/appointments/payments)
  → old JSON left in place, untouched, as rollback capability, until the
    new path has been trusted in production for a real observation
    window
```

None of this is built in Phase 0. This section exists so the next phase
doesn't have to re-derive the sequence from scratch.

## Running Alembic — read this before you type `alembic upgrade head`

`alembic/env.py` reads `DATABASE_URL` the same way `core/memory.py`
does, including calling `load_dotenv()` — so running `alembic upgrade
head` locally applies Phase 0's tables to **whatever database your
`.env` file's `DATABASE_URL` currently points at**. If that's a real
deployment's production Postgres, that's exactly the intended
coexistence design (see above) — but be deliberate about knowing which
database that is before running it, the same way you'd be deliberate
about any other command that writes to a real deployment.

For local development/testing without touching any real database,
override the environment variable for just that command:

```bash
DATABASE_URL="sqlite:///./phase0_local.db" python -m alembic upgrade head
```

This is exactly how Phase 0's own verification was done — every Alembic
command run during Phase 0's development explicitly overrode
`DATABASE_URL` to a throwaway local file, specifically to avoid touching
the real Beyond Pain/founder production database while proving the
migration works.

## Do not rebuild — systems Phase 0 (and this whole V2 effort) must not touch without a real reason

- **Specialist orchestration** (`services/specialist_orchestration.py`) — routing, tool-calling, and synthesis pattern is sound; V2 tenancy needs only a `clinic_id` parameter threaded through it, not a rewrite.
- **Approval/execution engine** (`services/integration_manager_v21.py`) — fingerprint-based dedup, correct approval-status reconciliation; genuinely well-designed and recently fixed for durability.
- **Automation qualification logic** (`scheduler/run_scheduled_checks.py`'s 14 `@check` functions) — idempotent, tested, correct; needs tenant-scoping at the data-access boundary only.
- **Integration adapter API-calling logic** (`integrations/*.py`) — the actual WhatsApp/Gmail/Calendar API calls, dry-run pattern, error handling; needs per-clinic credential *resolution*, not different API-calling code.
- **The current Streamlit UI** — no concrete blocker to keeping it; no React rewrite is warranted by anything found so far.
- **Docker/docker-compose foundation** — simple, correct, appropriate for its current scope.

See the full V2 audit (delivered separately, not in this repo) for the complete gap map this list is drawn from.
