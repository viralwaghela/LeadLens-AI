# V2 Phase 9 — Production Hardening

The final foundational migration phase. No product redesign — this is
about making the system Phases 0-8.1 built operationally trustworthy:
observable, recoverable, and honest about what's still not done.

## 1. Production risk map

| Area | Risk | Notes |
|---|---|---|
| Live audit view global by default | **CRITICAL if multi-org** | Fixed in Phase 8.1 behind `LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` (defaults OFF) — safe today (one org), a real leak the moment a second org is provisioned without turning it on. `config_validation.py` now FAILs this combination explicitly. |
| `appointment_reminder()`'s 24hr auto-send credential resolution | **HIGH if multi-org**, **CLOSED this phase** | Fixed — see §8 below. |
| `marketing-site/api/lead.py` organization-unaware | **HIGH if multi-org**, **CLOSED this phase** | Fixed — see §9 below. |
| No automated backup/restore procedure existed | **HIGH** | Closed this phase — `scripts/backup_database.py` / `scripts/restore_validate.py` + `docs/BACKUP_RESTORE.md`. |
| No health check / readiness command | **HIGH** | Closed this phase — `scripts/health_check.py` / `scripts/production_readiness.py`. |
| No centralized config validation | **MEDIUM** | Closed this phase — `core/config_validation.py`. |
| Scheduler check-function execution content still zero-argument-implicit for anything NOT threaded through `context` (only `appointment_reminder`'s 24hr branch was closed this phase; the rest were already fixed in Phase 8.1) | **LOW** (all 14 checks already accept/use `context`) | Reconfirmed clean — see §7. |
| 18 `services/*.py` files appear unreachable from any live code path | **LOW** (dead code, not a safety risk) | Documented, not deleted — see §15. |
| No dependency vulnerability scanning existed | **MEDIUM** | Closed this phase — `pip-audit`, clean run, added to CI. |
| GitHub Actions scheduler could theoretically overlap runs | **LOW** | Assessed — see §23; tenant-scoped idempotency is the real safety net regardless. |
| Structured logging/error categories didn't exist | **LOW-MEDIUM** (operability, not a data-safety issue) | Closed — `core/observability.py`. |
| No documented rollback runbook | **MEDIUM** | Closed this phase — `docs/PRODUCTION_RUNBOOK.md`. |

Everything above CRITICAL/HIGH that could be closed within Phase 9's
scope was closed. Nothing CRITICAL remains open for the current
single-organization Beyond Pain deployment.

## 2. Health check system

`scripts/health_check.py` — read-only, ten checks (process, DB
connectivity, migration drift, legacy memory_store reachability,
organization resolution, scheduler readiness, credential-encryption key
validity, integration configuration counts, Jarvis/LLM configuration
presence, centralized config validation), rolled up into
HEALTHY/DEGRADED/UNHEALTHY. Never sends a real message, never prints a
secret. Exit code 0 for HEALTHY/DEGRADED, 1 for UNHEALTHY (standard
health-probe convention — DEGRADED still serves traffic).

## 3. Startup config validation

`core/config_validation.py::validate_configuration()` — inspects env
vars only, never prints a value, returns OK/WARN/FAIL findings. Flags:
missing auth entirely (open door), V2 auth on without a session secret,
tenant features on without a credential-encryption key, and the
specific unsafe combination the Phase 8 audit found (multi-org intent
without audit-tenant-scoping turned on) as a hard FAIL. Not wired into
`app.py`'s own startup automatically — that would be a live-app behavior
change requiring its own sign-off; call it explicitly from
`scripts/health_check.py` / `scripts/production_readiness.py`, or from
a deployment's own startup hook if an operator chooses to.

## 4-6. Logging, error categories, correlation ids

`core/observability.py` — plain `logging` plus conventions, not a new
framework: `ErrorCategory` (DATABASE, TENANT_RESOLUTION, AUTHENTICATION,
AUTHORIZATION, INTEGRATION, SCHEDULER, CRM_SYNC, JARVIS, CONFIGURATION),
`new_run_id()` (a short correlation id for one scheduler pass/backfill/
integration action), `log_event()` (one structured line: category,
operation, run_id, organization_id, actor_type, a safe detail string).
`safe_context()` strips any credential-shaped key before it ever reaches
a log line as a defense-in-depth backstop. Not yet wired into every
existing call site (that would be a large, mechanical, higher-risk
sweep across the codebase) — available for new/touched code going
forward, and for a future dedicated logging-adoption pass.

## 7. Scheduler reliability

Reconfirmed via code inspection and the existing Phase 8.1 test suite:
`run_all_checks()` catches each check's own exception
(`_run_one_check()`) so one check's failure never aborts the others;
`resolve_scheduler_organizations()`'s multi-org loop runs every check
for every enumerated organization independently — a failure inside one
organization's check invocation is caught the same way and doesn't
abort the remaining organizations. Idempotency remains tenant-scoped
(Phase 8.1). `main()`'s exit code (1 if any check's detail starts with
"FAILED") already distinguishes partial failure from full success.
Retry policy: none inside a single run (by design — the scheduler is
invoked on a fixed interval by GitHub Actions/cron, so the next
scheduled run is the retry; see §23 for why an in-process retry loop
would risk duplicate sends).

## 8. `appointment_reminder()` 24hr auto-send gap — CLOSED

`services/appointment_messaging.py`'s `send_appointment_confirmation()`
and `send_appointment_rsvp_reminder()` now accept an optional `context:
TenantContext | None = None`. `scheduler/run_scheduled_checks.py`'s
`appointment_reminder()` now passes its own `context` straight through.
When supplied: patient lookup (`get_record(..., organization_id=...)`),
clinic-name resolution (org-scoped `OrganizationSettings` when that flag
is on, else the legacy global fallback), and WhatsApp credential
resolution (`get_whatsapp_client(context)` directly, never re-resolved
to the transitional default) are all scoped to that exact organization.
Verified: Organization A's send genuinely attempts A's own (test) token;
Organization B, unconfigured, correctly falls back to a safe dry-run/
simulated response and never touches A's credential or patient data —
see `tests/test_phase9_production_hardening.py`. `ui/patient_crm.py`'s
live booking-confirmation call site is unaffected (omits `context`,
unchanged implicit resolution via the live session).

## 9. Marketing-site lead endpoint — CLOSED (explicitly tenant-aware)

`marketing-site/api/lead.py` now resolves exactly one trusted
organization *before* any write, via
`_resolve_target_organization_id()`:

1. `LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG` configured (recommended
   for any deployment past the single-clinic default): resolved against
   a real, ACTIVE `organizations` row — never trusted blindly.
2. Not configured, and exactly one organization exists: that
   organization, unambiguously (the common single-clinic case — no
   extra configuration required).
3. Not configured, and more than one organization exists: refuses to
   write at all (`AmbiguousMultiOrgDatabaseError`, unchanged from Phase
   8.1) rather than guess.

The legacy `memory_store` write remains authoritative and unconditional
(same "legacy remains authoritative" contract every Phase 3+ dual-write
in this codebase uses — this is what the live CRM UI reads today). A
best-effort shadow write additionally lands the lead in the relational,
organization-scoped `leads` table via raw SQL (mirroring
`services/relational_sync_service.py`'s upsert-by-external_id shape
without importing the ORM cross-project, per this file's own stated
constraint) — never blocks the legacy write on failure.
`scripts/verify_multi_org_readiness.py`'s readiness gate now reports OK
when the slug is configured, FAIL when it isn't and more than one
organization exists — no false PASS.

## 10-11. Backup and restore

See `docs/BACKUP_RESTORE.md` for the full procedure.
`scripts/backup_database.py`: Postgres → `pg_dump -Fc`; local SQLite →
sqlite3's own online backup API for the relational schema, plus
`core.memory.backup_now()` (already existed, reused rather than
reimplemented) for the legacy memory_store file. `scripts/restore_validate.py`:
restores a local SQLite relational backup into an isolated temp copy and
runs the same migration-drift + tenant-integrity checks
`production_readiness.py` runs against it — proven via
`tests/test_phase9_production_hardening.py`'s round-trip test. Postgres
restore validation is a documented manual procedure (`pg_restore` into
an isolated throwaway database, then point `restore_validate.py --database-url`
at it) rather than an automated script, since spinning up a throwaway
Postgres server isn't something a validation script should do
implicitly.

## 12. Data integrity verifier

`scripts/verify_multi_org_readiness.py` extended with
`membership_orphan_check()` (every membership resolves to a real user
and organization) and `shadow_sync_health()` (unresolved Phase 3/4
shadow-write failures, total ever recorded, read mismatches recorded) —
both counts/booleans only, never patient data. Existing
`cross_org_fk_check()` (composite-FK sanity) and per-organization CRM/
approval/queue/integration/Jarvis-memory/audit row counts unchanged.

## 13. Legacy migration flag classification

| Flag | Introduced | Classification | Recommended production value |
|---|---|---|---|
| `LEADLENS_V2_DUAL_WRITE_ENABLED` | Phase 3 | KEEP TEMPORARILY | ON once backfilled + verified (prerequisite for any read/tenant flag below) |
| `LEADLENS_V2_READ_<ENTITY>` (9 flags) | Phase 4 | KEEP TEMPORARILY | Entity-by-entity, only after `verify_v2_crm_read_parity.py` is clean for that entity |
| `LEADLENS_V2_READ_COMPARE` | Phase 4 | SAFE TO REMOVE LATER | OFF once every entity's cutover is trusted (it's a pre-cutover verification aid, not a steady-state feature) |
| `LEADLENS_V2_READ_FAILSAFE_LEGACY` | Phase 4 | DANGEROUS TO REMOVE YET | OFF (masks cutover defects) — keep available as an emergency lever only |
| `LEADLENS_V2_TENANT_CONTEXT_ENABLED` | Phase 5 | KEEP TEMPORARILY | ON for any multi-org deployment (prerequisite for audit/scheduler/settings tenant-scoping) |
| `LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` | Phase 6 | DANGEROUS TO REMOVE YET | OFF once all clinics have migrated credentials (transitional bridge, restricted to the default org only) |
| `LEADLENS_V2_AUTH_ENABLED` | Phase 7 | KEEP TEMPORARILY | ON for any real deployment — prerequisite for every RBAC/tenant-scoping mechanism |
| `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED` | Phase 8 | KEEP TEMPORARILY | ON for genuine multi-org; **do not enable for single-org Beyond Pain without a reason** — legacy path is simpler and already correct for one org |
| `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED` | Phase 8 | KEEP TEMPORARILY | ON alongside CRM-tenant-authoritative |
| `LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED` | Phase 8 | KEEP TEMPORARILY | ON alongside CRM-tenant-authoritative |
| `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` | Phase 8 | KEEP TEMPORARILY | ON only once every organization's `automations_enabled` has been deliberately reviewed |
| `LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` | Phase 8.1 | **SAFE TO DEFAULT ON** for any multi-org deployment | ON whenever more than one organization exists — `config_validation.py` now hard-FAILs the unsafe combination |
| `LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG` | Phase 9 | KEEP (per-deployment config, not a boolean flag) | Set explicitly for any deployment past the single-clinic default |

None are safe to *remove* yet (delete the flag and its branch) — every
one still has a live legacy code path serving the current single-org
production deployment. "Safe to default on" (audit-tenant-authoritative)
means safe to turn *on*, not safe to delete the flag/legacy branch.

## 14. Legacy `memory_store` dependency map

| Section | Classification | Notes |
|---|---|---|
| `company` (settings) | LEGACY FALLBACK | Relational-authoritative when `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED` is on and a live org resolves; legacy global dict otherwise (Phase 8). |
| `clinic_patients`/`clinic_appointments`/`clinic_packages`/`clinic_package_templates`/`clinic_payments`/`clinic_therapists`/`clinic_progress_notes`/`clinic_leads`/`clinic_corporate_clients` | LEGACY FALLBACK | Relational-authoritative (read+write) when `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED` is on; legacy JSON otherwise (Phase 8). Always dual-written to the relational shadow when `LEADLENS_V2_DUAL_WRITE_ENABLED` is on regardless. |
| `security_audit_log` | LEGACY FALLBACK (write), LEGACY FALLBACK (read) | Write always unconditional (compatibility path); read relational-authoritative when `LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` is on (Phase 8.1). |
| `scheduler_alert_ledger` | LEGACY FALLBACK | Idempotency check reads the legacy section; shadow-synced into `SchedulerAlertLedgerEntry` (organization-scoped) when tenant-context is on, but nothing reads the relational copy back yet — legacy remains the actual gate. |
| `approvals` / execution queue (`services/integration_manager_v21.py`'s own JSON section) | **LEGACY ONLY** | Never migrated to relational-authoritative reads — `execution_rows()` (the live UI's Action Center reader) still always reads the legacy list. Relational `Approval`/`ExecutionQueueItem` exist only as an organization-scoped shadow copy (Phase 5/6.1), useful for verification/audit tooling (this phase's scripts), not yet the live source of truth. |
| `reports` (owner-facing Tier-1 alerts) | **LEGACY ONLY** | No relational equivalent exists; not part of any Phase 0-8.1 tenant-scoping work. |
| `daily_logs` | **LEGACY ONLY** | Operational log only (integration send attempts) — no relational equivalent, low sensitivity. |
| `tasks` / `decisions` / `completed_tasks` | **LEGACY ONLY** | No relational equivalent; not CRM data, not tenant-scoped anywhere yet. |
| `scheduler_runs` (run log) | **LEGACY ONLY** | No relational equivalent. |
| Jarvis learning memory (`preferences`/`recommendations`/`outcomes`/`executions`) | LEGACY FALLBACK | Relational-authoritative (`JarvisLearningRecord`) when `LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED` is on and a live org resolves; the shared legacy JSON file is used for the default organization always, and is explicitly NEVER touched by a non-default organization when the flag is on (Phase 8's specific fix). |

**Nothing in `core/memory.py` is DEAD** — every section above is either
actively read/written by the legacy path (still the default everywhere)
or by the relational path when its flag is on. Do not delete
`memory_store` in Phase 9 or any near-term phase — the approvals/queue,
reports, daily_logs, tasks, decisions, and scheduler_runs sections have
**no relational equivalent at all** yet; removing the legacy store would
delete live product functionality, not just a migration artifact.

## 15. Dead code — documented, not deleted

Static analysis (grep for any import of each module anywhere in the
tracked tree, cross-checked against `dashboard.py`'s actual routing —
confirmed `ui/clinic_jarvis.py` defines its own like-named
`show_patient_intelligence()`/`show_revenue_intelligence()`/
`show_therapist_intelligence()` functions independently of the
identically-named service modules below) found 18 `services/*.py` files
with no reachable import anywhere in `app.py`, active Streamlit routing,
`scheduler/`, `scripts/`, or the live integration adapters:

`services/appointment_intelligence.py`, `autonomous_execution.py`,
`chief_of_staff_agents.py`, `clinic_briefing.py`, `followup_engine.py`,
`google_calendar_connector.py`, `google_sheets_connector.py`,
`integration_registry.py`, `json_utils.py`, `memory_patient_engine.py`,
`morning_brief_engine.py`, `morning_brief_v2.py`,
`patient_churn_engine.py`, `patient_intelligence.py`,
`revenue_forecasting.py`, `revenue_intelligence.py`,
`therapist_intelligence.py`, `utilization_engine.py`.

**Not deleted this phase** — high-confidence but not exhaustively proven
(no check was made for string-based/dynamic imports, e.g. inside a
prompt template or a lazy-loaded plugin registry). Per the explicit
Phase 9 instruction ("if deleting poses risk, document it instead"),
recommend a small, dedicated follow-up session to delete these with a
final reachability double-check immediately before removal, mirroring
the Phase 7-era cleanup's own methodology.

## 16. Test side effects

Reviewed: every test file under `tests/` that touches `core/memory.py`
either goes through `tests/_bootstrap.py`'s guard (hard-fails if
`DATABASE_URL` is already set at import time, strips it from every
`load_dotenv()` call afterward) or explicitly monkeypatches
`business_memory.DATABASE_FOLDER`/relevant module `_ENGINE` attributes
to a `tmp_path`/in-memory SQLite engine (the pattern every Phase 5-9
test file in this session established and reused). No test mutates a
tracked runtime JSON file or the real configured `DATABASE_URL`. One
genuine near-miss caught and avoided during this phase's own manual
testing (not a committed test): an ad-hoc verification script that
didn't patch `DATABASE_FOLDER` read/wrote against the real local dev
database — a reminder embedded in this doc, not a code change, since
it was never a committed test.

## 17. CI hardening

Added: a Phase 9 production-readiness/health-check/multi-org-readiness
smoke-run step (against the CI job's own freshly-migrated, empty test
database — proves the aggregator runs clean end to end, not a
meaningful multi-org integrity check against real data) and a
`pip-audit` dependency-vulnerability scan step (report-only for now —
see §18; currently clean). Lint/type-checking: still deliberately
skipped (no tool configured in this repository, and introducing one
that would immediately surface hundreds of pre-existing, unrelated
findings is explicitly out of scope per the Phase 9 spec's own
instruction not to fake compliance by adding a tool nobody can act on).

## 18. Dependency security

Ran `pip-audit -r requirements.txt` locally: **no known vulnerabilities
found**. Added as a CI step (report-only — a finding produces a
`::warning::` annotation, not a failed build, until/unless a real
finding needs the team's own upgrade-risk decision).

## 19. Secret scanning

Unchanged from Phase 3's existing pattern-based scan (`sk-...`, AWS
`AKIA...`, private key headers, Slack tokens) across every tracked file
except `*.md` and the CI workflow file itself. Reviewed for Phase 9:
every fake credential this phase's own new tests use is either an
obviously-synthetic Fernet-generated key (`Fernet.generate_key()`, never
hardcoded) or a string like `"A-TOKEN"`/`"sk-not-a-real-secret-do-not-print-..."`
that cannot match any real provider's key format and is explicitly
named to be obviously fake.

## 20-21. Deployment and rollback

See `docs/PRODUCTION_RUNBOOK.md`.

## 22. Database migration safety

`alembic upgrade head` from empty and from the current production head
both verified clean this phase (CI already ran both before this
session; reconfirmed locally with a throwaway SQLite target).
`alembic check`: no drift. No migration in this repository performs an
irreversible operation (no dropped column with data loss, no
non-additive enum removal) — every migration to date has been purely
additive (new tables/columns). Not rewriting any historical migration
file, per instruction.

**Operational finding, not a code change**: this session's own
read-only smoke testing against the developer's real, `.env`-configured
`DATABASE_URL` found that database is currently two migrations behind
head (`973f082fdc6e`, code expects `692395df9cde`). Not fixed here —
running `alembic upgrade head` against a database this session doesn't
have explicit confirmation is safe/production is out of scope for an
unattended action; flagged for the founder to run
`alembic upgrade head` (or `python scripts/health_check.py` to confirm
first) at their own discretion.

## 23. Background job locking

Assessed: `.github/workflows/scheduler.yml` (unchanged this phase, not
re-created) — GitHub Actions' own default `concurrency` behavior for a
`schedule`-triggered workflow does not automatically prevent overlap if
a run takes longer than the interval between triggers. No explicit
`concurrency:` group was added this phase — the real, existing safety
net is `already_flagged()`/`_mark_flagged()`'s idempotency ledger (now
organization-scoped, Phase 8.1), which makes a genuinely-overlapping
second run a no-op for anything already flagged, not a duplicate send.
Recommend adding a `concurrency: {group: scheduler, cancel-in-progress: false}`
block to the workflow as a belt-and-braces measure in a focused, tested
follow-up — not done here since it touches deployment-infrastructure
YAML outside this session's ability to verify against a real GitHub
Actions run.

## 24-25. External API retries and timeouts

Reviewed `integrations/whatsapp_service.py`, `gmail_service.py`,
`calendar_service.py`: none currently implement bounded retry logic.
Not added this phase — per the Phase 9 spec's own explicit warning
("Never retry an external send in a way that can easily duplicate
messages without idempotency protection"), and because none of these
adapters currently distinguish retryable (timeout/429/5xx) from
non-retryable (auth/permission/invalid-payload) failures at the HTTP
layer — building that distinction correctly is real, non-trivial work
better scoped as its own reviewed change than folded into an
already-large Phase 9. Documented as a known gap, not silently
"handled." Timeout audit: the underlying `requests`/provider-SDK calls
in each adapter were not confirmed to set explicit timeouts in this
pass — flagged as a specific, scoped follow-up (a mechanical, low-risk
change once verified) rather than guessed at.

## 26-27. Failure observability / operator CLI

`scripts/production_readiness.py` is the single operator command this
phase adds — see §34. It surfaces failed migrations, tenant-integrity
problems, unresolved shadow-sync failures, and every readiness-gate
finding in one place. No new UI, no new dashboard — a CLI report, per
the explicit "prefer a safe operator CLI/report over a large new UI"
instruction.

## 28. Backup/restore test

`tests/test_phase9_production_hardening.py::test_backup_and_restore_validate_round_trip`
— provisions a real relational + legacy SQLite pair, backs both up,
restores the relational backup into an isolated temp copy, and confirms
`restore_validate.py` reports a coherent PASS/WARN/FAIL with the
expected sections present. No production credentials required.

## 29-30. Performance and indexes

Reviewed the existing index set (`core/db/models/clinic.py`,
`operations.py`): `patient_id`/`therapist_id`/date columns already
indexed on appointments/payments/progress notes; `organization_id` is
already the leading column of an existing composite unique index on
every `OrgScopedMixin` table's `(organization_id, id)` and
`(organization_id, external_id)` constraints, so organization-scoped
queries already have index coverage without adding a redundant plain
index. `SecurityAuditEvent.organization_id` indexed (Phase 5).
Scheduler ledger's `(check_name, item_key)` both indexed. No new index
added this phase — none of the reviewed query patterns showed an
obvious missing-index gap. A dedicated load test against ~1k
patients/several-thousand appointments was not run this phase (would
need a realistic synthetic-data generation script of its own, out of
scope for this pass) — flagged as a follow-up, not fabricated.

## 31. Security review

Re-ran the full test suite (415 tests, including every Phase 5-8.1
adversarial isolation test: A cannot read/write B, A cannot use B's
credentials, A cannot approve/execute B's queue items, A cannot access
B's audit trail via the live reader, A cannot influence B's scheduler
run, RBAC direct-backend-call denial, session-tampering rejection) — all
green, no regressions from this phase's changes.

## 32. Production flags — recommended values

See §13's table for the full per-flag recommendation. Summary for the
two deployment shapes:

- **Single-clinic compatibility (current Beyond Pain default)**: every
  V2 flag OFF except `LEADLENS_V2_AUTH_ENABLED` (recommended on for real
  per-user login) — this is safe, tested, and the current production
  state.
- **True multi-org**: `LEADLENS_V2_AUTH_ENABLED`,
  `LEADLENS_V2_TENANT_CONTEXT_ENABLED`,
  `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED`,
  `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED`,
  `LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED`, and
  `LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` all ON together (the
  first six are interdependent — turning on a subset leaves a real gap,
  which `config_validation.py` now checks for);
  `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` ON only once every
  organization's `automations_enabled` has been deliberately reviewed;
  `LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG` set per-deployment if that
  clinic uses the public booking form.

## 33. Known limitations — honest final accounting

| Limitation | Classification |
|---|---|
| `execution_rows()` (Action Center UI) and `security_service.audit_event()`'s write path remain legacy-global; only reads were made tenant-scoped where fixed | IMPORTANT |
| Scheduler's 14 check functions' own qualification/business logic unchanged — organization threading only, not a rewrite; retry/backoff logic doesn't exist | LOW |
| No bounded-retry/timeout audit completed for WhatsApp/Gmail/Calendar/LLM calls | IMPORTANT |
| No `concurrency:` guard added to the scheduler GitHub Actions workflow | LOW (idempotency is the real safety net) |
| 18 `services/*.py` files are high-confidence dead code, not deleted | LOW |
| No load/performance test against realistic data volumes was run | DEFERRED FEATURE (this pass) |
| Logging conventions (`core/observability.py`) exist but aren't adopted across every existing call site yet | LOW |
| No public self-signup, billing, SSO, or broad UI work — explicitly out of scope, not a gap | DEFERRED FEATURE |
| Developer's local `.env`-configured database is 2 migrations behind head | IMPORTANT (operational, not code — run `alembic upgrade head`) |

**No BLOCKER remains for the current single-organization Beyond Pain
production deployment.** The IMPORTANT items above matter specifically
for a genuine second-clinic production rollout, not for continued
single-clinic operation.

## Phase 9.1 addendum — multi-organization credential health + readiness hardening

An independent audit of this phase (see the "Audit LeadLens CareOS V2
Phase 9" request that followed) returned PARTIAL PASS, finding two real
observability defects — both in the *checking* code, never in the
credential-handling code itself (`resolve_credentials()` already failed
closed correctly for every scenario tested):

1. `check_credential_encryption()` decided whether the encryption key was
   *required* from feature-flag intent (`LEADLENS_V2_AUTH_ENABLED` /
   `LEADLENS_V2_TENANT_CONTEXT_ENABLED`) rather than actual stored
   `OrganizationIntegration` state — a configured integration with a
   missing/wrong key, with both flags off, could be reported HEALTHY.
2. `check_integration_configuration()` (via
   `integration_manager_v21.integration_statuses()`) only ever inspected
   the transitional default organization — a broken credential belonging
   to any other organization was invisible to `health_check.py` /
   `production_readiness.py`.

**Fix — one shared, real-decryption-attempt data layer.**
`services/integration_credentials.py` gained:

- `credential_encryption_key_required(session) -> bool` — a
  data-driven existence check: True iff ANY `OrganizationIntegration`
  row, in any organization, any status, has non-empty
  `encrypted_credentials`. Never looks at a feature flag.
- `assess_integration_health(session, *, include_inactive_organizations=False) -> list[IntegrationHealthEntry]`
  — enumerates ACTIVE organizations (or all, when explicitly asked) ×
  every `IntegrationProvider`, joined against actual integration rows in
  two queries total (no N+1). For each `ACTIVE`-status row with stored
  ciphertext, attempts a real, read-only decryption and classifies
  `decryptable: True/False/None` plus `error_category`. A `DISABLED`
  integration is reported as such and never attempted (intentionally
  off is not "broken"). An `UNCONFIGURED` provider is reported
  `decryptable=None` (optional, never counted as broken). Output is
  structurally safe: organization id/slug, provider, status,
  decryptable, error_category only — never a secret value.
- `IntegrationHealthEntry` — the frozen dataclass both fields above and
  every caller use.

Both `scripts/health_check.py::check_credential_encryption()` and
`check_integration_configuration()`, plus a new
`scripts/production_readiness.py::_section_integration_credentials()`
section, now derive their verdict entirely from this same real,
per-organization decryption-attempt data — so the two checks can never
disagree with each other, and a broken credential belonging to any
organization (not just the default) is surfaced by name (safe slug),
with the finding's own section never masked by an unrelated
`production_readiness.py` section failing.

**A third defect was found and fixed during this phase's own
adversarial self-testing, before the report above was even written**:
the first draft of `check_credential_encryption()` treated "a
syntactically well-formed key is present" as sufficient for HEALTHY —
so a syntactically-valid but *wrong* key (not the one that actually
encrypted the stored data) was reported HEALTHY. Fernet's authenticated
encryption cannot distinguish "wrong key" from "corrupted ciphertext" at
the API level (both raise the same `InvalidToken`/
`CredentialDecryptionError`), so both are now classified together as
`error_category in ("key_unavailable", "decryption_failed")` and both
fail the check — a real operational failure regardless of which of the
two it actually is.

**Verified, via `tests/test_phase9_1_credential_health.py` (13 tests)
plus a standalone A–F adversarial scenario battery run directly against
`scripts/production_readiness.py`** (no credentials at all → PASS/not
required; healthy credential + correct key → PASS; non-default-org
credential + missing key → FAIL, correctly named; corrupted ciphertext
→ FAIL; one healthy org + one broken non-default org → only the broken
one surfaced as FAIL; multiple healthy orgs → PASS; unconfigured
optional providers alone → PASS, never a false failure). No false PASS
occurred for any broken scenario; no false FAIL occurred for any healthy
or merely-unconfigured scenario.

**Not touched, not needed**: no schema/migration change (confirmed via
a clean `alembic upgrade head` against a throwaway database), no change
to `resolve_credentials()`'s fail-closed behavior or Phase 6 tenant
credential isolation, no new flags, no UI change, no billing/OAuth/CRM/
Jarvis/scheduler-redesign work — none of that was in scope and none was
started, per this phase's explicit instruction.

**A known engine-lifecycle testing pitfall, encountered again in this
phase's own test suite, worth remembering for future test authors**: a
test that monkeypatches `core.db.session.make_engine` to always return
one shared, already-open Engine object breaks the moment any exercised
code path calls `.dispose()` on it — the first `.dispose()` corrupts the
engine for every later call in the same test (catastrophic for SQLite
`:memory:`, whose default connection pool holds the database's only
connection). First fixed this way in Phase 8.1's
`run_all_checks()`; this phase's own new test file initially hit the
identical failure mode and was fixed the same way other Phase 9 tests
already do it — pointing `get_database_url()` at a real temp-file SQLite
database instead, so each internal `make_engine()`/`.dispose()` pair
constructs and safely tears down its own independent Engine object
against the same underlying file.

## Phase 9.1.1 addendum — ERROR-state credential health classification

An independent audit of Phase 9.1 found one confirmed defect:
`assess_integration_health()` only decrypt-attempted rows with
`status == ACTIVE`. But `resolve_credentials()` (the real adapter read
path) transitions a row to `status == ERROR` the moment a *live*
decryption failure occurs — after that transition the row fell out of
the health check's classify branch entirely (`decryptable=None`,
`error_category=None`), identical to an intentionally `DISABLED`
integration. `check_credential_encryption()`,
`check_integration_configuration()`, and
`production_readiness.py`'s `integration_credentials` section could all
report HEALTHY/PASS for a credential already known broken by a real
failed send.

**Fix (`services/integration_credentials.py::assess_integration_health()`)**:
widened the decrypt-attempt condition from `status == ACTIVE` to
`status in (ACTIVE, ERROR)`. Traced first: `resolve_credentials()` is
the *only* place in this codebase that ever sets `IntegrationStatus.ERROR`,
and it does so only immediately after a real decryption failure —
`last_error_code` is always `"key_unavailable"` or `"decryption_failed"`.
This module makes no live provider API calls, so there is no
"ERROR from connectivity" case to conflate with a credential problem —
re-attempting decryption (rather than trusting the persisted
`last_error_code`) keeps ACTIVE and ERROR rows classified through the
exact same single code path. Still fully read-only: no
`session.flush()`/`commit()`, never rewrites status/ciphertext/
last_error fields, never silently re-activates a row — an ERROR row
whose key has since been fixed correctly reports `decryptable=True`
while its administrative `status` stays `"ERROR"` until an operator
explicitly reconfigures it (status and decryptability are reported as
two independent fields, on purpose).

Verified via `tests/test_phase9_1_1_hardening.py` (14 tests, including
the exact audit regression sequence — configure, rotate to a wrong key,
trigger one real `resolve_credentials()` call exactly as a live send
would, confirm the row is genuinely `ERROR`, then confirm all three
surfaces report the problem) and confirmed the regression test fails
against the pre-fix condition and passes against the fix (both states
verified directly, not merely asserted). No schema change, no new
flags, no regression across the full suite (442 tests, up from 428).
