# V2 Phase 4 — CRM relational read cutover

Read this before touching `services/crm_read_router.py`,
`services/clinic_data_service.py`'s `_read_rows()`, or
`scripts/verify_v2_crm_read_parity.py`. Read `docs/V2_COEXISTENCE.md`,
`docs/V2_PHASE1_IDENTITY.md`, `docs/V2_PHASE2_JARVIS_MEMORY.md`, and
`docs/V2_PHASE3_CRM_DUAL_WRITE.md` first — this document assumes those
rules still apply. **Phase 4 is the first phase that changes live READ
behavior.** Read this whole document before enabling any
`LEADLENS_V2_READ_*` flag anywhere, including staging.

## 1. Read-path audit (performed before writing any Phase 4 code)

`services/clinic_data_service.py` exposes exactly 7 read functions:
`list_records()`, `get_record()`, `search_records()`,
`records_with_patient_names()`, `patient_profile()`,
`patient_risk_summary()`, and `clinic_metrics()`. Tracing them: the
last 6 are all built on `list_records()` (directly or transitively —
`search_records()` calls `list_records()`; `records_with_patient_names()`
calls `list_records()`; `patient_profile()` calls `get_record()` +
`list_records()`; `patient_risk_summary()` calls `list_records()` +
`patient_profile()`; `clinic_metrics()` calls `list_records()` +
`patient_risk_summary()`), and `list_records()` itself is built on one
function: `_read_rows()`. **`_read_rows()` is the single true
"get every row for this entity from storage" chokepoint** — every
caller in the repo (`ui/patient_crm.py`, `ui/jarvis_mode.py`,
`ui/crm_dashboard.py`, `services/jarvis_context.py`,
`services/appointment_messaging.py`, `services/live_workflow_service.py`,
`scheduler/run_scheduled_checks.py`) goes through these 7 functions,
confirmed via a full-repo grep for every caller of each one. Routing
`_read_rows()` alone therefore covers every reader in the application —
the same minimal-surface-area principle Phase 3 used for writes (two
call sites covered every mutation).

No direct-memory-key read bypass was found (a repo-wide grep for
`clinic_patients`/`clinic_appointments`/etc. outside
`clinic_data_service.py` and `core/memory.py` turns up nothing).
`services/jarvis_context.py` imports `list_records` from
`clinic_data_service.py` directly — it is not a bypass, and it
automatically benefits from (and is affected by) read-cutover exactly
like every other caller.

## 2. Read router — `services/crm_read_router.py`

```
clinic_data_service._read_rows(entity)
    -> crm_read_router.read_rows(entity, legacy_reader)
        -> entity's LEADLENS_V2_READ_<ENTITY> flag OFF (default)
           -> legacy_reader() — byte-for-byte Phase 3 behavior
           -> if LEADLENS_V2_READ_COMPARE is on: ALSO read relational,
              normalize, compare, record any mismatch — but still
              RETURN the legacy result
        -> flag ON
           -> read relational, normalize to legacy shape, return it
           -> on relational failure: record it; raise unless
              LEADLENS_V2_READ_FAILSAFE_LEGACY is set
```

`get_record()` needed no separate relational lookup — it already works
by scanning `list_records()`'s output for a matching ID, so it
transparently inherits correctly-routed behavior with zero additional
code. This is a deliberate simplicity choice (same O(n) scan cost
profile as before cutover, on both stores) — the safest option for a
high-risk phase, not a missed optimization.

## 3. Entity flags — all default OFF

| Entity | Flag | Legacy entity exists? |
|---|---|---|
| leads | `LEADLENS_V2_READ_LEADS` | yes |
| corporate_clients | `LEADLENS_V2_READ_CORPORATE_CLIENTS` | yes |
| services | `LEADLENS_V2_READ_SERVICES` | **no** — see below |
| therapists | `LEADLENS_V2_READ_PRACTITIONERS` | yes |
| package_templates | `LEADLENS_V2_READ_PACKAGE_TEMPLATES` | yes |
| packages | `LEADLENS_V2_READ_PACKAGES` | yes |
| appointments | `LEADLENS_V2_READ_APPOINTMENTS` | yes |
| progress_notes | `LEADLENS_V2_READ_PROGRESS_NOTES` | yes |
| payments | `LEADLENS_V2_READ_PAYMENTS` | yes |

`package_templates` was added as its own independent flag during
implementation — it's a genuinely separate legacy entity from
`packages` (its own `ENTITY_META` row, its own UI callers), not named
in the original flag-name examples, but "each entity must be
independently controllable" requires it not silently share
`LEADLENS_V2_READ_PACKAGES`'s switch.

`services` has **no legacy counterpart** — confirmed in Phase 0's own
audit (service names are free text on Appointment/Package records, not
a distinct entity) and reconfirmed here (`clinic_data_service.py`'s
`ENTITY_META` has no `"services"` key, so `_read_rows("services")` is
never called by anything). `LEADLENS_V2_READ_SERVICES` and a standalone
`crm_read_router.read_services()` function exist (per the spec's
explicit request to implement support for the entity), but nothing in
the live app calls it — this is prepared, dormant infrastructure, not
a live cutover path, exactly like Phase 0's `Service` model itself.

Turning any flag OFF immediately restores legacy behavior for that
entity — no data migration, no restart-time cost — `read_rows()` checks
the flag on every call.

## 4. Shadow-compare mode — `LEADLENS_V2_READ_COMPARE`

Default OFF. When on, every `_read_rows()` call for a **not-yet-cut-over**
entity also runs the relational read in the background, normalizes it,
and compares field-by-field against the legacy result — recording any
disagreement in `core/db/models/shadow_sync.py`'s `ReadMismatch` table
— but always returns the legacy result regardless of outcome. This is
the mechanism for production canary verification before flipping an
entity's real read flag. Comparison excludes `created_at`/`updated_at`
(see "Known field-level divergence" below) and compares numeric fields
via `Decimal(str(x))` to avoid float-noise false positives.

`ReadMismatch` never stores the record's own payload — only
organization, entity, operation, record ID (if safe), a mismatch
category (`count_mismatch` / `missing_from_relational` /
`field_mismatch:<field>`), and a short detail string.

## 5. Read-failure policy — `LEADLENS_V2_READ_FAILSAFE_LEGACY`

Default OFF, by design. Once an entity's read flag is on, relational is
authoritative for it — a relational read failure is recorded via the
Python `logging` module and then **allowed to propagate** (e.g. surfaces
as a Streamlit error) rather than being silently masked. This is
deliberate: a canary/production defect that gets silently hidden behind
an automatic legacy fallback is much harder to notice than an error
that's immediately visible to whoever is watching the entity right
after cutover. Set `LEADLENS_V2_READ_FAILSAFE_LEGACY=true` to restore
fail-safe-to-legacy behavior instead, if that tradeoff is preferred for
a specific deployment — document that choice locally if you make it.

Before cutover (flag off), a relational read failure during
compare-mode is always caught and logged, never allowed to break the
(legacy) read that's actually being returned.

## 6. Organization resolution

Every relational query goes through the same
`core.identity.default_organization.resolve_default_organization_id()`
helper Phase 2/3 already use — get-or-create by
`LEADLENS_DEFAULT_ORG_SLUG` (default `"default-clinic"`), resolved
fresh per call, never cached, never accepting a caller-supplied
`organization_id` (`crm_read_router.read_rows()`'s signature has no
such parameter — `tests/test_phase4_crm_read_cutover.py::test_read_router_never_accepts_caller_supplied_organization_id`
proves this directly). Every relational query filters by
`organization_id` explicitly — confirmed by source review of every
`_read_relational_rows()` branch.

## 7. Ordering

Relational reads are ordered by `id ASC` (the auto-increment primary
key). Because `scripts/backfill_v2_crm.py` inserts rows in legacy list
order, and the live dual-write hook (Phase 3) appends new rows in the
same relative order writes happen, `id ASC` reproduces legacy list
(insertion) order in the normal case — proven directly by
`tests/test_phase4_crm_read_cutover.py::test_multiple_records_and_ordering_matches_legacy_insertion_order`.
Callers that impose their own explicit ordering on top (e.g.
`patient_profile()` sorting appointments/progress notes by visit date,
newest first) are unaffected either way, since they re-sort whatever
list they're handed — verified by
`test_appointment_chronological_ordering_via_patient_profile`.

## 8. Known field-level divergence: `created_at` / `updated_at`

Relational rows get their own `created_at`/`updated_at` timestamps at
backfill or dual-write time (via `TimestampMixin`'s Python-side
defaults) — **not** the original legacy timestamp string. This is an
accepted, documented divergence, not a defect: nothing in the live app
sorts or filters by these two fields (confirmed in the read-path
audit — `patient_profile()` and every other caller that cares about
"when" uses a business date field like `appointment_date`/`visit_date`,
never `created_at`), and `compare_rows()` deliberately excludes both
fields from shadow-compare equality checks for exactly this reason —
matching them would produce constant false-positive mismatches with no
diagnostic value.

## 9. Null / missing-field semantics

Legacy JSON rows omit a key entirely for any optional field that was
never set (e.g. a patient created without a phone number has no
`"phone"` key at all, not `"phone": ""`). The relational normalizers
always include every field, using the same default a typical
`row.get(key, default)` caller already expects (`""` for strings, `0`
for numbers, `False` for booleans) — verified safe by a repo-wide grep
for `"field" in row`-style key-presence checks on CRM dicts, which
found **none**; every caller uses `.get()` with a default. This was a
deliberate, verified choice, not a casual replacement of "missing" with
"invented data" — see `tests/test_phase4_crm_read_cutover.py`'s
flag-on tests, which assert against real caller-observable behavior,
not raw dict equality.

`archived` (a boolean legacy rows carry alongside `status="Archived"`,
set together by `archive_record()`) is reconstructed by the normalizer
whenever `status` is `"Archived"`, so `list_records()`'s own
archived-filtering logic (which checks both keys) behaves identically
regardless of source — verified by
`test_patient_archived_status_preserved_and_filtered`.

## 10. Cutover order (production activation, not code)

```
1. leads
2. corporate_clients
3. services            (dormant — no legacy source to cut over yet)
4. therapists/practitioners
5. patients
6. packages
7. appointments
8. progress_notes/treatments
9. payments             (LAST — highest financial risk)
```

No flag is enabled anywhere in code by this phase — every
`LEADLENS_V2_READ_*` flag defaults OFF, and enabling one is a
deployment-time (environment/secrets) decision, never a code change.

## 11. Per-entity activation runbook

For **each** entity, in the order above, one at a time:

```
1. Confirm full storage parity: scripts/verify_v2_crm_parity.py (Phase 3)
2. Enable LEADLENS_V2_READ_COMPARE=true only (entity's own read flag
   stays OFF) — production traffic keeps reading legacy, relational
   reads run silently alongside it
3. Exercise the actual application (open the relevant CRM screen,
   let Jarvis/scheduler run a cycle) so real read traffic flows
   through compare mode
4. Run scripts/verify_v2_crm_read_parity.py for this entity — confirm
   zero mismatches. Also check the ReadMismatch table directly for
   anything compare-mode recorded during step 3's live traffic.
5. Enable this entity's LEADLENS_V2_READ_<ENTITY>=true
6. Verify the UI screen(s) that show this entity
7. Verify Jarvis if it consumes this entity (patients, appointments,
   payments, leads — see docs/V2_COEXISTENCE.md's field mapping)
8. Verify automation/scheduler if it reads this entity
   (scheduler/run_scheduled_checks.py is a heavy consumer of
   appointments, therapists, patients, payments, leads,
   corporate_clients)
9. Run scripts/verify_v2_crm_read_parity.py again
10. Monitor (logs + the ReadMismatch/ShadowSyncFailure tables) for a
    real observation window
11. Only then proceed to the next entity in the order above
```

**Never enable more than one high-risk entity's read flag at once.**
"High-risk" here means patients, appointments, and payments
specifically — leads/corporate_clients/therapists/package_templates
carry materially less blast radius if something is subtly wrong.

## 12. Rollback

```
LEADLENS_V2_READ_<ENTITY>=false
```

For the specific entity having a problem. No database restoration is
needed — the relational data is simply not read anymore; Phase 3's
dual-write keeps it in sync in the background regardless. Multiple
entities can be rolled back independently and in any order. Dual-write
(Phase 3) is never affected by a read-flag rollback and should keep
running throughout.

## 13. Write path — unchanged

Phase 3's authoritative-write contract is untouched by Phase 4: every
CRM mutation still writes legacy first, then attempts a relational
shadow write, exactly as before. Phase 4 only changes *which store
answers a read* — confirmed via `git diff` showing zero changes to
`services/relational_sync_service.py`'s write logic or
`clinic_data_service.py`'s `add_record()`/`update_record()`/`archive_record()`.

## Do not implement (Phase 4 non-goals, confirmed untouched)

Turning off legacy writes, deleting legacy CRM storage, live
multi-tenant organization switching, live Phase 1 authentication
cutover, live RBAC enforcement, Jarvis tenant authorization, tenant
scheduler/approval queue, per-clinic integration credentials,
billing/subscriptions, new AI features, UI redesign.

## Do not rebuild

Same list as the prior phases' docs, unchanged by Phase 4: specialist
orchestration, the approval/execution engine, automation qualification
logic, integration adapters, the current Streamlit UI, the Docker
foundation, Phase 3's write-side dual-write logic itself.
