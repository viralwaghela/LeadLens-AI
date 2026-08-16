# LeadLens CareOS — Backup & Restore

## What needs backing up

Two logical data stores, which may be one or two physical files/
databases depending on deployment:

1. **The relational V2 schema** (`core/db/` — organizations, users,
   memberships, tenant-scoped CRM shadow/authoritative tables, Jarvis
   learning memory, integration credentials, audit events, approvals,
   execution queue, scheduler ledger).
2. **The legacy `memory_store`** (`core/memory.py` — company profile
   when not org-scoped, CRM data when not tenant-authoritative, owner-
   facing reports, tasks, decisions, daily logs, scheduler run log,
   security audit log when not tenant-authoritative). See
   `docs/V2_PHASE9_PRODUCTION_HARDENING.md` §14 for exactly which
   sections are legacy-only vs. relational-authoritative today.

On **Postgres** (Supabase/Neon/any standard Postgres — the recommended
production backend), both live in the *same* database — one backup
covers both. On **local SQLite** (no `DATABASE_URL` set), they are two
separate files.

Do not assume your hosting provider's own automatic backups (Supabase's
point-in-time recovery, Neon's branching, etc.) are sufficient on their
own without ever having tested a restore from them — provider backups
are a good baseline, but this repository's own backup/restore tooling
gives you an independent, portable copy you control and have actually
validated.

## Backup procedure

```bash
python scripts/backup_database.py --out backups/ --label <context>
```

`<context>` is a short label like `pre-deploy`, `nightly`, or
`pre-migration` — it's embedded in the output filename alongside a UTC
timestamp.

- **Postgres**: shells out to `pg_dump -Fc` (custom format — the format
  `pg_restore` expects; supports selective/parallel restore, unlike a
  plain SQL dump). Requires `pg_dump` on `PATH`.
- **Local SQLite**: the relational schema is backed up via SQLite's own
  online backup API (safe to run while the app is live — takes a
  consistent snapshot even mid-write, never just copies the raw file);
  the legacy `memory_store` file is backed up via the pre-existing
  `core.memory.backup_now()` (same safe API, reused rather than
  reimplemented).

The script never logs `DATABASE_URL`, a password, or any other secret —
every error message is scrubbed before printing (see
`scripts/backup_database.py::_scrub()`).

### Retention recommendation

- Daily automated backup, retained 14 days.
- Weekly backup, retained 90 days.
- A manual backup immediately before any deploy that includes a
  migration, retained until the next scheduled backup confirms the
  deploy is stable (at least 48 hours).
- Store backups somewhere other than the same host/region as the live
  database — a hosting-provider outage that takes down the database
  should not also take down its backups.
- Encrypt backups at rest if your storage location doesn't already
  (most object storage does by default) — a backup file contains the
  same sensitive patient data the live database does.

### Verification procedure

A backup file existing is not proof it's restorable. After every backup
(or at minimum, after every backup taken before a risky change):

```bash
# Local SQLite relational backup:
python scripts/restore_validate.py --backup backups/leadlens_<label>_relational_<timestamp>.db

# Postgres backup — restore into an isolated, throwaway database first:
createdb leadlens_restore_test
pg_restore -d leadlens_restore_test backups/leadlens_<label>_<timestamp>.dump
python scripts/restore_validate.py --database-url postgresql://.../leadlens_restore_test
```

`restore_validate.py` never touches the original backup file (for the
SQLite path, it copies it into a temporary directory first) and never
touches any live/production database — it only ever operates on the
isolated restored copy you point it at.

## Restore workflow

**Never test a restore against production.** Always restore into an
isolated environment first:

1. **Backup**: confirm you have a recent, verified-restorable backup
   (see above) before touching anything.
2. **Restore into isolation**:
   - Postgres: `createdb` a new, throwaway database and `pg_restore`
     into it — never restore over the live database directly, even
     during a genuine disaster-recovery restore. Stand up the restored
     copy, verify it, *then* cut the application over to it (or restore
     it as a new primary once verified, per your hosting provider's own
     failover procedure).
   - SQLite: copy the backup file to a new path.
3. **Alembic compatibility**: `DATABASE_URL=<restored> python -m alembic check`
   — confirm the restored schema matches what the current code expects.
   If it's behind, `alembic upgrade head` against the *restored copy*
   first (never skip this and point live traffic at an out-of-date
   schema).
4. **Health check**: `DATABASE_URL=<restored> python scripts/health_check.py`
   — confirm HEALTHY/expected-DEGRADED against the restored copy.
5. **Tenant sanity verification**:
   `DATABASE_URL=<restored> python scripts/verify_multi_org_readiness.py`
   — confirm organization counts, cross-org FK check, and membership
   orphan check all look right for the point in time the backup was
   taken.
6. Only after all four steps pass: cut the application over to the
   restored database (update `DATABASE_URL` in the live deployment's
   configuration), following your hosting platform's own procedure for
   doing so safely.

## Backup/restore test coverage

`tests/test_phase9_production_hardening.py::test_backup_and_restore_validate_round_trip`
exercises the full local-SQLite round trip end to end (provision a real
schema → back it up → restore into isolation → validate) using a
temporary database, no production credentials required. Run it as part
of the normal test suite (`pytest tests/ -v`) — it is not a separate
manual-only check.
