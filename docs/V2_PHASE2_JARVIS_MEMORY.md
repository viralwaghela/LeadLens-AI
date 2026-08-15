# V2 Phase 2 — Jarvis learning-memory durable storage

Read this before touching `services/jarvis_memory.py`,
`core/db/models/jarvis.py`, or `scripts/migrate_jarvis_memory_to_db.py`.
Read `docs/V2_COEXISTENCE.md` and `docs/V2_PHASE1_IDENTITY.md` first —
this document assumes those rules still apply.

## What changed, and why this phase is different from Phase 0/1

Phase 0 and Phase 1 built dormant infrastructure — nothing live read or
wrote through `core/db/` or `core/identity/`. **Phase 2 is the first
phase where that stops being true.** `services/jarvis_memory.py` is a
genuinely live module (imported by `services/jarvis_context.py`,
`services/integration_manager_v21.py`, `services/learning_memory_v22.py`,
`services/outcome_learning_service.py`, `ui/chief_of_staff_workspace.py`)
and now actually reads and writes `core/db/models/jarvis.py`'s
`JarvisLearningRecord` table. Every other live file — `core/auth.py`,
`app.py`, `dashboard.py`, `core/memory.py`, the CRM, the scheduler, the
approval/execution engine, the integrations — is still completely
untouched; only this one storage path changed, and it changed
internally only. See "What every caller sees" below.

## The storage design

```
read:   DB (JarvisLearningRecord, scoped to the default organization)
          -> if zero rows exist for that organization (not migrated yet)
             or the DB is unavailable
          -> legacy JSON file (data/learning/learning_memory.json)

write:  legacy JSON file, always, unconditionally, first
          -> then best-effort mirror into the DB
```

### Why the JSON write is permanent, not "just during transition"

`services/jarvis_context.py` has its own **independent** direct read of
`data/learning/learning_memory.json` (its `LEARNING_FILE` constant, used
for a provenance/"source status" display — record counts and a loaded
flag shown to prove groundedness, not the actual data fed to the model,
which goes through `jarvis_memory.relevant_memory()`). Phase 2 is
storage-migration-only and does not touch `jarvis_context.py`, so the
legacy JSON file must keep being accurate forever, not just until a
one-time migration finishes. This was found during Phase 2's own
required "identify any direct filesystem assumptions" step — it is a
real, load-bearing dependency, not a hypothetical one.

Every public write in `jarvis_memory.py` therefore writes the JSON file
first, unconditionally, using the exact same atomic tempfile-write logic
that existed before Phase 2 — durability never regresses even if the
database is completely unreachable. The DB write is attempted
afterward, best-effort.

### Why reads prefer the DB

Once an organization has at least one migrated/written row, the DB is
treated as authoritative and the JSON file is not read for that
organization (it is still being kept up to date, for
`jarvis_context.py`'s sake, just not read back). Before that point — a
fresh deployment, or one where the migration script hasn't run yet —
`load_learning_memory()` falls back to reading the JSON file exactly as
it always has.

## Durable model: `JarvisLearningRecord`

Reused, not rebuilt — Phase 0 added this table specifically anticipating
this migration (see its own docstring history). One row per *authored*
record (preference, recommendation, outcome, or execution); "patterns"
stays derived/computed on every read via `_derive_patterns()`, exactly
as before, never independently persisted — matching Phase 0's explicit
design intent and this phase's "do not over-normalize" instruction.

| Column | Meaning |
|---|---|
| `organization_id` | Tenant scope (see "Organization readiness" below) |
| `record_type` | preference / recommendation / outcome / execution |
| `external_id`, `fingerprint` | The row's own legacy `id` field (e.g. `"PREF-A1B2C3D4E5"`) — already unique and stable, reused directly rather than inventing a second identifier scheme |
| `payload` | The full row dict, JSON-encoded |
| `created_at` / `updated_at` | Added in Phase 2 (renamed from Phase 0's single `recorded_at`) — rows get updated in place now (e.g. a preference's value changing) |

`UniqueConstraint(organization_id, record_type, fingerprint)` — the same
fingerprint can exist in two different organizations without collision
(`tests/test_phase2_jarvis_memory.py::test_organization_a_memory_isolated_from_organization_b`
proves this directly against the schema).

## Organization readiness (not live tenant routing)

There is no per-request/per-user organization context anywhere in the
live app yet (see `CLAUDE.md`'s multi-tenancy gap). `jarvis_memory.py`
resolves a single, fixed default organization
(`DEFAULT_ORGANIZATION_SLUG`, env-overridable via
`LEADLENS_DEFAULT_ORG_SLUG`, defaulting to `"default-clinic"`) via
get-or-create, fresh on every DB call rather than cached — this is
bootstrap plumbing to satisfy the schema's `organization_id NOT NULL`
requirement in a genuinely single-tenant deployment, **not** live tenant
routing. The schema itself is tenant-ready (composite uniqueness scoped
per organization) even though only one organization is ever actually
used by the live app today.

## What every caller sees

`load_learning_memory()`, `save_owner_preference()`,
`track_recommendation()`, `record_recommendation_outcome()`,
`record_action_execution()`, `relevant_memory()`, `memory_summary()` —
every signature and return shape is byte-for-byte unchanged. Callers
(`jarvis_context.py`, `integration_manager_v21.py`,
`learning_memory_v22.py`, `outcome_learning_service.py`,
`chief_of_staff_workspace.py`) needed zero code changes and have zero
diff in this phase — confirmed via `git diff`.

## Failure behavior

- **DB unavailable on write** (connection failure, or the Phase 2
  migration hasn't been applied to this deployment's database yet, so
  the table doesn't exist): caught (`SQLAlchemyError`), logged at ERROR
  level with a clear message via the standard `logging` module — never
  silently swallowed into a fake "success". The JSON write already
  happened first and unconditionally, so **no data is lost**.
- **DB unavailable on read**: caught the same way, falls back to
  reading the JSON file — Jarvis keeps working exactly as it did before
  Phase 2 existed.
- **Malformed JSON file**: `load_learning_memory()`'s legacy-JSON
  fallback returns a fresh/empty structure rather than crashing (same
  tolerant behavior as before Phase 2). The **migration script** is
  deliberately stricter here — it reports a clear error rather than
  silently treating corruption as "nothing to migrate".

## Migration and verification: `scripts/migrate_jarvis_memory_to_db.py`

Explicit, manual, idempotent. Not run automatically by anything — the
live `jarvis_memory.py` DB path already works correctly without it ever
running (new writes go to the DB regardless; this script's purpose is
moving *existing* history in sooner, and proving the two stores agree).

```bash
python scripts/migrate_jarvis_memory_to_db.py --dry-run
python scripts/migrate_jarvis_memory_to_db.py
python scripts/migrate_jarvis_memory_to_db.py --verify
```

- Reads and validates the legacy JSON file (missing file, malformed
  JSON, and a non-object top level are all reported clearly, not
  silently treated as empty).
- For every row, inserts a `JarvisLearningRecord` **only if** no row
  with that `(organization, record_type, fingerprint)` already exists —
  an existing DB row (possibly newer, e.g. written by the live app after
  Phase 2 deployed but before this script ran) is reported as
  `already_present` and left untouched, never overwritten.
  `tests/test_phase2_jarvis_memory.py::test_migration_never_overwrites_newer_db_record`
  proves this by mutating a DB row after migration and confirming a
  second migration run leaves the mutation intact.
- Preserves each row's original timestamp (parsed from its own
  `created_at`/`recorded_at` field) rather than stamping "migrated just
  now" over real history.
- Never touches or deletes the legacy JSON file.
- Safe to rerun any number of times — the second run onward reports
  everything as `already_present`.
- `--verify` runs no writes: it compares legacy JSON entry counts against
  DB entry counts per record type, and for every row present in both,
  compares a payload hash — any missing-from-DB row or hash mismatch is
  reported clearly, with an overall `PASS`/`MISMATCH` verdict.

## Rollback

No destructive DB changes are required to roll back. Two options,
fastest first:

1. **Kill switch** — set `LEADLENS_JARVIS_MEMORY_DB_DISABLED=1` in the
   environment and restart. `jarvis_memory.py` immediately reverts to
   exactly its pre-Phase-2 behavior: JSON file only, zero DB reads or
   writes. Because the JSON file has been kept continuously up to date
   by the permanent compatibility write (see above), this loses no data
   — the file is already current at the moment of rollback.
2. **Code revert** — revert the commit(s) that changed
   `services/jarvis_memory.py`. The legacy JSON file is untouched and
   still current either way.

The database schema itself is never rolled back as part of this — the
`jarvis_learning_records` table can simply sit unused again, exactly as
it did during Phase 0/1, with no cleanup required.

## Test-harness safety net (`tests/_bootstrap.py`)

The Phase 2 audit found `tests/TEST_PHASES_21_TO_23.py` calling
`services/learning_memory_v22.py`'s `record_learning_outcome()` without
redirecting `jarvis_memory.STORE`, silently mutating the tracked
`data/learning/learning_memory.json` on every test run (visible via
`git status` after any test suite run before this fix). Rather than fix
that one file and hope every future test remembers to redirect storage
itself, `tests/_bootstrap.py` — imported first by every test file, per
existing repo convention — now sets `LEADLENS_LEARNING_MEMORY_PATH` and
`LEADLENS_V2_DATABASE_URL` to a private per-process temp directory
before anything can import `jarvis_memory.py`, structurally closing this
class of mistake for every current and future test file. Individual
test files (`tests/test_jarvis_memory.py`,
`tests/test_approval_actions.py`, `tests/test_phase2_jarvis_memory.py`)
still monkeypatch `jarvis_memory.STORE`/`_ENGINE` directly for full
per-test isolation — the `_bootstrap.py` change is a safety net
underneath that, not a replacement for it.

## Known tradeoffs / technical debt

- **Full-collection resync on every write.** `_write_to_db()` re-upserts
  all rows across all four collections on every save, rather than
  diffing for the one row that actually changed. Deliberately simple,
  correct, and cheap at this deployment's actual scale (a single
  clinic's Jarvis memory — dozens to low hundreds of rows). If usage
  ever grows large enough for this to matter, a future phase should
  switch to touching only the changed row(s).
- **Default-organization resolution is not cached**, by design (a
  cached ID would go stale across test-engine swaps and, eventually,
  across any real multi-organization work) — an extra `SELECT` (and,
  once, an `INSERT`) per DB call. Negligible at this scale.

## Do not rebuild

Same list as `docs/V2_COEXISTENCE.md`'s and `docs/V2_PHASE1_IDENTITY.md`'s,
unchanged by Phase 2. Phase 2 adds nothing to this list itself — it
_is_ the migration Phase 0's `core/db/models/jarvis.py` was built in
anticipation of.
