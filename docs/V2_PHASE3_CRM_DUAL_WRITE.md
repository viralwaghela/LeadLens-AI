# V2 Phase 3 — CRM relational dual-write migration

Read this before touching `services/clinic_data_service.py`,
`services/relational_sync_service.py`, `core/db/models/shadow_sync.py`,
or `scripts/{backfill,repair}_v2_crm.py` / `scripts/verify_v2_crm_parity.py`.
Read `docs/V2_COEXISTENCE.md`, `docs/V2_PHASE1_IDENTITY.md`, and
`docs/V2_PHASE2_JARVIS_MEMORY.md` first — this document assumes those
rules still apply. **Phase 3 is the first phase that intentionally
changes a live CRM write path** — read this whole document before
enabling dual-write anywhere, including in a test/staging deployment.

## 1. CRM mutation-surface audit (performed before writing any Phase 3 code)

`services/clinic_data_service.py`'s `ENTITY_META` defines exactly 9
legacy entities: `patients`, `appointments`, `packages`,
`package_templates`, `payments`, `therapists`, `progress_notes`,
`leads`, `corporate_clients`. Every mutation goes through exactly two
functions: `add_record()` (create) and `update_record()` (update);
`archive_record()` is a thin wrapper that calls `update_record()` with
`status="Archived"` — there is no separate archive/delete code path to
hook. This means **two call sites cover every legacy CRM mutation**.

Traced every caller (`grep` for `add_record(`/`update_record(`/`archive_record(`
across the repo, then confirmed by reading `ui/patient_crm.py` in full):

| Entity | Create | Update | Archive |
|---|---|---|---|
| patients | ✓ | ✓ | ✓ (explicit `archive_record`) |
| appointments | ✓ | ✓ | via `update_record(status=...)` — no dedicated UI archive button |
| package_templates | ✓ | — (not exposed in UI) | ✓ (explicit `archive_record`) |
| packages | ✓ | ✓ | via `update_record(status=...)` |
| payments | ✓ | — (create-only; no update/archive path exists anywhere in the live app) | — |
| therapists | ✓ | ✓ | via `update_record(status=...)` |
| corporate_clients | ✓ | ✓ | via `update_record(status=...)` |
| leads | ✓ | ✓ | via `update_record(status=...)` |
| progress_notes | ✓ | — (create-only) | — |

Every one of these is in `ui/patient_crm.py` — the only UI CRM entry
point. `scheduler/run_scheduled_checks.py`, `services/appointment_messaging.py`,
and `services/live_workflow_service.py` all import
`services.clinic_data_service` but call only its **read** functions
(`list_records`, `get_record`, `records_with_patient_names`) — no
automation mutates clinic data. This is why Phase 3's dual-write hook
living at `clinic_data_service.py`'s two write functions is sufficient
to cover every legacy caller including every automation, with zero
changes to any automation itself (spec section 19).

### Known bypass — documented, not fixed in Phase 3

**`marketing-site/api/lead.py`** (a separate Vercel serverless function
for the marketing site's booking form) writes new leads directly via
raw `psycopg2`, bypassing `core/memory.py` and
`services/clinic_data_service.py` entirely — see that file's own
docstring: "Deliberately self-contained rather than importing
core/memory.py or services/clinic_data_service.py... those live outside
this Vercel project's root directory." It mirrors `memory_store`'s
locking pattern and writes directly into `payload["clinic_leads"]`.

**Consequence:** leads created via the marketing site's booking form do
**not** get dual-written to the relational shadow store at creation
time. They still land in `memory_store` (the same authoritative legacy
store `clinic_data_service.py` reads), so `scripts/backfill_v2_crm.py`
run periodically (or `scripts/repair_v2_crm.py --entity leads --all`)
picks them up like any other pre-Phase-3 legacy row. Fixing this bypass
itself — e.g. giving the Vercel function its own path into
`relational_sync_service`, or routing it through a real API instead —
is out of scope for Phase 3 (it would mean touching a separately
deployed system with its own database-connection library, a materially
bigger and riskier change than a storage-migration phase). Flagged here
per the task's explicit "any bypass must be documented" requirement.

## 2. Authoritative-write contract

```
legacy write (core/memory.py / memory_store) — commits and returns
        ↓ (only after the above succeeded)
relational shadow synchronization (services/relational_sync_service.py)
        ↓
success: relational row upserted
failure: caught, classified, recorded (ShadowSyncFailure) — legacy
         write is NOT rolled back, the CRM operation already succeeded
```

`core/memory.py` is not modified in any way. No SQLAlchemy operation
runs inside `core/memory.py`'s own transaction — the shadow sync opens
its own, separate SQLAlchemy session (`core/db/session.session_scope()`)
strictly after `save_records()` has already returned.

## 3. `services/relational_sync_service.py`

The single, isolated component holding all relational-persistence logic
for CRM entities. `clinic_data_service.py` calls exactly one function,
`sync_upsert(entity, row, operation=...)`, from exactly two places (the
tail of `add_record()` and `update_record()`) — it does not know
SQLAlchemy or organizations exist. `sync_upsert()` **never raises**:
every failure is caught, classified via `_classify_error()`, and
recorded via `record_sync_failure()` — see section 6 below. This
component can be deleted or the two call sites removed without
`clinic_data_service.py` needing any other change, satisfying the
"removable/disableable independently" requirement.

## 4. Kill switch — `LEADLENS_V2_DUAL_WRITE_ENABLED`

Read once at import time in `relational_sync_service.py`. **Defaults to
OFF** (`False` unless explicitly set to `1`/`true`/`yes`) — a deliberate
choice, not an oversight: Phase 3 is the first phase to touch a live
write path, and no production deployment has had a backfill or parity
check run against it at the moment this code first ships. Turning it
off:

- restores exactly Phase 2's CRM write behavior (legacy-only)
- has **no effect on any CRM read** — Phase 3 does not touch
  `list_records`, `get_record`, `search_records`, `patient_profile`, or
  any other read function; reads were never wired to the relational
  side in the first place
- has **no effect on Jarvis** — `jarvis_context.py` and
  `jarvis_tools.py` still read exclusively through
  `clinic_data_service.py`'s read functions, untouched by this phase
- has **no effect on authentication** — `core/auth.py` is untouched
- requires **no restart-time destructive migration** — flipping the
  variable and restarting the process is the entire procedure

## 5. Organization resolution

`core/identity/default_organization.py` (extracted in Phase 3 from
Phase 2's own private copy of this logic, so Jarvis's learning-memory
shadow store and Phase 3's CRM relational shadow store resolve to the
**same** organization row — there is only one clinic per deployment
today). `resolve_default_organization_id(session)`: get-or-create by
`DEFAULT_ORGANIZATION_SLUG` (env-overridable via
`LEADLENS_DEFAULT_ORG_SLUG`, defaults to `"default-clinic"` — never
hard-coded to "Beyond Pain"). Called fresh on every DB access, not
cached, matching Phase 2's own reasoning (tests can swap engines
without stale-ID bugs; the extra `SELECT` is not a meaningful cost at
this deployment's scale). Callers can never inject a different
`organization_id` into a normal CRM mutation — `sync_upsert()`'s
signature takes only `(entity, row, operation)`, no organization
parameter; the organization is always resolved internally. If
resolution itself fails (DB unavailable), the shadow sync is recorded
as a failure exactly like any other — the legacy write already
succeeded and is unaffected.

## 6. Shadow-sync failure ledger — `core/db/models/shadow_sync.py`

`ShadowSyncFailure`: organization (nullable — resolution can itself
fail), entity, external_id, operation (`create`/`update`/`backfill`/`repair`),
error_category (`missing_parent` / `validation` / `db_error` /
`unsupported_entity` / `unknown`), a safe error_summary (exception type
+ generic message — **never the row's own data**, so patient names,
contact details, and financial amounts never land in this table),
resolved flag + timestamp. Written by `relational_sync_service.record_sync_failure()`,
used identically by the live hook, `backfill_v2_crm.py`, and
`repair_v2_crm.py`.

**Known limitation, verified directly (2026 Phase 3 audit's failure-injection
test):** this ledger can only be written to if the V2 database is
reachable *at all* — if the entire relational database is unreachable
(not just one table, or a missing parent row, but a full connection
failure), the attempt to record the failure fails too, and the gap is
silently absorbed (logged via Python `logging` only, not persisted).
The legacy write still succeeds and no data is lost — but in this
specific total-outage scenario, `scripts/repair_v2_crm.py --verify`
(which reads the failure ledger) will not show the gap.
`scripts/verify_v2_crm_parity.py` (which compares legacy and relational
data directly, not via the ledger) **will** still catch it. Operators
should treat a full parity run, not just an unresolved-failure count,
as the authoritative check after any suspected V2 database outage.

```bash
python scripts/repair_v2_crm.py --entity leads --record-id L-004
python scripts/repair_v2_crm.py --entity leads --all
python scripts/repair_v2_crm.py --all
python scripts/repair_v2_crm.py --verify   # report unresolved count only, no writes
```

Re-syncs from the **current legacy record** (`clinic_data_service.get_record()`
— the authoritative source), using the exact same `sync_one()` upsert
logic as the live hook and the backfill script — not a separate
implementation. On success, marks matching unresolved
`ShadowSyncFailure` rows resolved. Idempotent — re-running against an
already-correct record just re-confirms it (upsert, not insert).

## 8. Backfill — `scripts/backfill_v2_crm.py`

```bash
python scripts/backfill_v2_crm.py --dry-run
python scripts/backfill_v2_crm.py --organization my-clinic-slug
python scripts/backfill_v2_crm.py --entity patients --entity appointments
```

Reads every legacy entity via `clinic_data_service.list_records(entity, include_archived=True)`
— the same read path the live app uses, never touching `core/memory.py`
directly — in dependency order (`ENTITY_SYNC_ORDER`, below), and
upserts each row via the same `sync_one()` function everything else
uses. Never deletes. Idempotent. Does not hard-code any clinic name —
`--organization` defaults to the same `DEFAULT_ORGANIZATION_SLUG` every
other Phase 2/3 component resolves to.

### Migration order

```
leads → corporate_clients → therapists → package_templates   (independent)
  → patients                                                  (independent)
    → packages, appointments                                  (need patients; appointments also need therapists, optional)
      → progress_notes                                        (needs patients, optionally therapists)
        → payments                                             (needs patients, optionally packages)
```

`services` has no legacy counterpart (confirmed in Phase 0's own audit —
service names are free text on appointments/packages) and is not
backfilled. If a dependent entity's parent hasn't been synced yet
(shouldn't happen given this order, but is possible if `--entity` is
used to restrict a run), `sync_one()` raises `MissingParentError`,
caught and recorded per-row — the rest of the backfill continues rather
than aborting.

## 9. Parity verification — `scripts/verify_v2_crm_parity.py`

```bash
python scripts/verify_v2_crm_parity.py
```

Read-only. Per entity: counts, external IDs present in one store but
not the other, a small set of "important" fields (status plus one or
two key values — financial amounts compared as `Decimal`, never
`float`) for every record present in both stores, and relationship IDs
(e.g. an appointment's relational `patient_id` resolves back to the
same legacy `patient_id` it was created with). Report format:

```
Patients
legacy: 124
relational: 124
matched: 124
mismatch: 0
```

Never prints row payloads — only counts, IDs, and the specific field
values being compared.

## 10. Identifier preservation

Every relational entity's `external_id` column holds the exact legacy
business ID (`"P-001"`, `"A-014"`, etc. — unchanged since Phase 0).
`relational_sync_service._get_or_create()` upserts by
`(organization_id, external_id)`, never generating a new identifier for
an existing legacy record. Relational primary keys (`id`, auto-increment
integers) exist only for the database's own internal use (composite FK
targets) and are never exposed to or depended on by any legacy-facing
code, Jarvis, reports, or automations.

## 11. Financial integrity

`_to_decimal()` converts every amount via `Decimal(str(value))`, never
`Decimal(float(value))` — `str()` first avoids picking up binary
floating-point representation error (e.g. `19.1` stored and read back
exactly as `Decimal("19.1")`, not `Decimal("19.099999999999999645...")`).
`payments.amount` and `package_templates.price` are `Numeric(10, 2)`
columns (already defined in Phase 0). Proven directly by
`tests/test_phase3_crm_dual_write.py::test_payment_amount_exact_decimal_no_float_drift`.

## 12. Concurrency

Every relational table has a `UniqueConstraint(organization_id, external_id)`
(Phase 0). Two concurrent shadow-sync attempts for the same record can
therefore never produce a duplicate row at the database level — the
second racing write hits the constraint and raises `IntegrityError`,
caught by the same failure-isolation path as any other shadow-sync
failure (recorded, repairable), never corrupting state or crashing the
legacy write that triggered it. `tests/test_phase3_crm_dual_write.py::test_concurrent_syncs_do_not_duplicate_or_lose_state`
exercises this directly with real threads against a shared file-based
SQLite database.

## 13. Rollback

```bash
LEADLENS_V2_DUAL_WRITE_ENABLED=false
```

Because CRM reads remain on the legacy system throughout Phase 3 (never
touched), disabling dual-write restores exactly Phase 2's CRM behavior
— no data migration back is needed, and none of the relational rows
already written need to be touched or deleted. The relational shadow
store simply stops receiving new writes; it can be left in place,
inspected, repaired, or dropped later at leisure.

## 14. Recommended production deployment sequence

```
1. Deploy Phase 3 code with LEADLENS_V2_DUAL_WRITE_ENABLED unset (off)
   — legacy CRM behavior is completely unchanged by this deploy alone.
2. Run scripts/backfill_v2_crm.py --dry-run, review the report.
3. Run scripts/backfill_v2_crm.py for real. This creates/confirms the
   bootstrap organization and populates the relational shadow store
   from current legacy data.
4. Run scripts/verify_v2_crm_parity.py — confirm PASS (0 mismatches)
   before proceeding.
5. Enable LEADLENS_V2_DUAL_WRITE_ENABLED=true and restart.
6. Canary: perform one controlled, low-risk CRM mutation in the live
   app (e.g. create one test lead), then:
7. Verify both stores directly — the legacy record via the normal CRM
   UI, the relational row via a quick manual query or
   scripts/verify_v2_crm_parity.py --entity leads.
8. Monitor scripts/repair_v2_crm.py --verify (or a periodic cron run of
   it) for unresolved shadow-sync failures going forward.
```

Do not flip the kill switch on at the same moment as the initial
deploy — steps 2–4 exist specifically so the first live dual-write
happens against a relational store already known to agree with legacy
data, not an empty or partially-migrated one.

## Do not implement (Phase 3 non-goals, confirmed untouched)

Relational CRM reads, legacy read cutover, live multi-tenant clinic
switching, live authentication/RBAC changes, tenant-aware Jarvis, tenant
scheduler/approval queue, per-clinic integration credentials,
subscription billing, new AI features, UI changes. Execution queue,
approval state, scheduler ledger, and integration credentials are not
migrated in Phase 3 — they belong to later tenant-scoping work.

## Do not rebuild

Same list as the prior phases' docs, unchanged by Phase 3: specialist
orchestration, the approval/execution engine, automation qualification
logic, integration adapters, the current Streamlit UI, the Docker
foundation. Phase 3 adds: do not wire `relational_sync_service` into
any CRM **read** path without that being its own explicit, discussed
phase (Phase 3 is write-only, by design).
