# LeadLens CareOS — Production Runbook

See `docs/V2_PHASE9_PRODUCTION_HARDENING.md` for the full design behind
every tool referenced here, and `docs/BACKUP_RESTORE.md` for backup/
restore specifically.

## Deployment checklist

1. `pytest tests/ -v` — full suite green.
2. `python -m alembic check` (against the target database) — no drift.
3. `python scripts/backup_database.py --out backups/ --label pre-deploy`
   — take a fresh backup before touching anything.
4. Deploy the new code (Streamlit Cloud / Render / Railway / Docker —
   whatever this deployment's own platform is).
5. If the migration head changed: `python -m alembic upgrade head`
   against the target database.
6. `python scripts/health_check.py` — confirm HEALTHY or an expected
   DEGRADED (e.g. "OPENAI_API_KEY not set" on a deployment that
   intentionally runs without it).
7. `python scripts/production_readiness.py` — confirm no FAIL.
8. Smoke-test login (both legacy and V2 paths if both are ever live
   during a transition).
9. Smoke-test CRM: open a patient record, confirm data loads.
10. Smoke-test Jarvis: ask one question, confirm a real (or expected
    templated-fallback) response.
11. Smoke-test approval/execution: confirm the Action Center loads and
    an existing pending item (if any) is visible.
12. Confirm the scheduler: check the most recent
    `.github/workflows/scheduler.yml` run succeeded, or trigger one
    manually and check its log.
13. Monitor errors for the following hour — application logs, and
    `services/tenant_operational_sync.py`'s shadow-sync failure table
    (`scripts/verify_multi_org_readiness.py`'s shadow-sync health line)
    if dual-write is enabled.

## Rollback runbook

**Code rollback** (a bad release) is separate from **data rollback** (a
bad migration or bad data change) — never conflate them. Most releases
only need the former.

### Application release rollback

Redeploy the previous known-good commit/tag on your hosting platform.
No database action needed unless that release also shipped a migration
(see below). Every V2 feature is gated behind an env-var flag, so a code
rollback that lands on an OLDER commit with a flag the newer commit
introduced simply ignores that flag/env var — safe by construction.

### Feature-flag rollback (the normal lever, not a code deploy)

Every V2 mechanism has its own independent flag, defaulting OFF — flip
it back off in your deployment's environment/secrets and restart the
app. No code change, no migration, immediate effect:

| Symptom | Flag to turn off |
|---|---|
| V2 login broken | `LEADLENS_V2_AUTH_ENABLED` — reverts to the legacy shared-password gate |
| CRM data looks wrong/missing after enabling tenant-authoritative mode | `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED` |
| Settings/company profile looks wrong | `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED` |
| Jarvis "forgot" its memory after enabling multi-org | `LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED` |
| Scheduler behaving unexpectedly across organizations | `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` |
| Audit log looks wrong | `LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` |
| An integration credential resolution issue | `LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` |
| Public lead form erroring | Unset `LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG` only if it was just misconfigured — check the slug matches a real, ACTIVE organization first; do not disable the endpoint's safety guard |

### Database migration rollback

Prefer a **forward fix** (a new, additive migration) over
`alembic downgrade` whenever practical — every migration in this
repository so far has been purely additive (new tables/columns, no data
loss on downgrade), but a downgrade still removes columns/tables that
may have accumulated real data since the upgrade ran. If a downgrade is
genuinely necessary:

1. `python scripts/backup_database.py --out backups/ --label pre-rollback`
2. `python -m alembic downgrade <previous_revision>`
3. `python scripts/health_check.py` to confirm the app still starts
   cleanly against the rolled-back schema.

Never run a downgrade against a database an application version newer
than the target revision is still actively writing to.

### Auth cutover rollback

Set `LEADLENS_V2_AUTH_ENABLED=false`. The legacy shared-password path is
preserved byte-for-byte and remains the rollback target for as long as
any deployment might need it (see `docs/V2_PHASE7_AUTH_CUTOVER.md`).
Both User/Membership rows (V2) and `APP_PASSWORD` (legacy) persist
regardless of which path is active — no data is lost switching either
direction.

### Integration configuration rollback

`services/integration_credentials.disable_integration()` sets a
provider's status to DISABLED for one organization without deleting the
stored (encrypted) credential — safer than leaving it ACTIVE with a
suspected-bad value, and instantly reversible
(`configure_integration()` to re-activate).

### CRM read-flag rollback

Turn off the specific `LEADLENS_V2_READ_<ENTITY>` flag for the affected
entity. Reads immediately revert to the legacy JSON list; the legacy
write path was never turned off in the first place (Phase 3's
authoritative-write contract), so there is no data to reconcile.

### Scheduler-enablement rollback

Set the affected organization's `automations_enabled` to `false` via
`core.identity.organization_profile_service.set_automations_enabled()`
(or leave `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` off entirely) — the
next scheduler run excludes that organization immediately.

## Production flag reference

See `docs/V2_PHASE9_PRODUCTION_HARDENING.md` §13 and §32 for the full
flag classification and recommended values for both single-clinic and
true multi-org deployment shapes.
