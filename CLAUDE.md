# LeadLens CareOS — Project Context

Read this before making changes. This explains what the product is actually
meant to be, not just what the code currently does — there's a gap between
the two in places, and it's important to know which is which.

## The vision

LeadLens is not a one-off CRM built for a single clinic. It's a product
meant to serve small-scale clinics, fitness centers, and similar small
service businesses broadly. "Beyond Pain" (a physiotherapy clinic in Malad,
Mumbai) is the founder's own business and the current example/demo data —
it is not the product's identity, and code should not assume it's the only
customer this will ever have.

**The CRM is not the product. Jarvis is the product.**

The actual thing being sold is an AI employee — Jarvis — who has a team of
his own and can be asked to do anything to help the business grow. The
name is a deliberate reference to Tony Stark's Jarvis: not just a capable
assistant, but a loyal one. The founder wants Jarvis to feel like the best
friend of the business — someone who only ever has the business's best
interests in mind, nothing else. This is a tone and trust design goal as
much as a technical one: how Jarvis phrases things, what he proactively
raises versus stays quiet on, how he owns mistakes, should all reflect
"I'm on your side," not "I'm a tool you're operating."

Practically, Jarvis's job is to take over tasks that are boring, repetitive,
or don't require human creativity or judgment — freeing the business owner
to spend their attention only on things that genuinely need a human. The
CRM (patient records, tasks, approvals, daily logs) exists as the substrate
Jarvis needs in order to actually do things for the business, not as a
feature in its own right.

## How the current code maps to that vision

- `services/specialist_orchestration.py` and the "agent team" — this is
  Jarvis's *staff*, not just a multi-agent implementation detail. Different
  specialists get consulted and their input synthesized into one voice:
  Jarvis's.
- `services/jarvis_context.py` — builds a privacy-filtered, grounded view
  of the real business for Jarvis to reason over, so he's never just
  making things up about the business.
- `services/integration_manager_v21.py` — the approval gate. This is the
  mechanism for Jarvis to actually *act* in the world (send a message,
  book something) rather than only talk, currently implemented
  conservatively: nothing external fires without a human approving it
  first. This is the literal implementation of "executes tasks that don't
  need human attention," deliberately gated for trust/safety right now.
- `integrations/calendar_service.py`, `gmail_service.py`,
  `whatsapp_service.py` — the actual hands Jarvis uses to act, with real
  dry-run and live modes.
- `core/memory.py` — the single source of truth for one business's data.
  Supports either local SQLite or an external Postgres database
  (Supabase/Neon) via a `DATABASE_URL` env var, chosen so the app's data
  survives regardless of what happens to the app's hosting.

## Where the code does NOT yet match the vision — known gaps

1. **Single-tenant, not multi-tenant.** The app currently holds exactly one
   business's data at a time (`core/memory.py` is built around one
   `memory_store` row). "One LeadLens, many clinics" is the real long-term
   product, but that requires a genuine multi-tenant rebuild — separate,
   isolated data per clinic — not a small tweak. Don't assume this is
   solved; don't quietly add multi-tenant-shaped code without discussing
   it first, since it's a significant architectural decision. **V2 Phase
   0 through 8 have been built** (a relational schema, an identity/
   authorization backend on top of it, Jarvis's learning-memory storage
   now genuinely running through this schema, CRM writes shadow-writing
   into the relational schema, CRM *reads* that can be routed to the
   relational schema too, entity-by-entity, behind flags that all
   currently default OFF, a `TenantContext` abstraction plus
   tenant-scoped shadow copies of approvals, the execution queue, audit
   events, and the scheduler ledger, all behind
   `LEADLENS_V2_TENANT_CONTEXT_ENABLED` (**defaults OFF**), encrypted,
   organization-scoped WhatsApp/Gmail/Calendar credentials with a
   transitional environment-variable fallback, behind
   `LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` (**defaults OFF**), a
   real per-user email/password login (Argon2id) plus backend RBAC
   enforcement across CRM/Jarvis/approvals/integration administration,
   behind `LEADLENS_V2_AUTH_ENABLED` (**defaults OFF**), and — Phase 8 —
   an explicit organization-provisioning CLI plus genuine
   organization-scoped CRM, Jarvis memory, settings, and audit-event
   storage (previously all resolved to the single transitional default
   organization no matter who was logged in, a gap Phase 8's own audit
   found and fixed), each behind its own independently-defaulted-OFF
   flag — see "V2 migration" below) but the scheduler's live check
   logic (per-organization *enumeration* now works when opted in;
   per-organization *execution content* inside the 14 check functions
   themselves does not yet), CRM reads/writes, Jarvis memory, settings,
   and audit events all remain legacy/opt-in exactly as before whenever
   their respective Phase 8 flag is off, and — critically —
   `LEADLENS_V2_AUTH_ENABLED` defaulting OFF means the **historical
   shared-password login remains the actual production gate** until an
   operator explicitly cuts over (see `docs/V2_PHASE7_AUTH_CUTOVER.md`'s
   runbook); a genuine second organization can now be provisioned and
   validated end-to-end (see `docs/V2_PHASE8_SAAS_ONBOARDING.md`), but
   there is still no *live production* multi-organization deployment —
   every Phase 8 flag defaults off — and no organization switcher beyond
   Phase 7's minimal picker; RBAC enforcement now exists and is tested,
   but sits dormant in every live deployment until `LEADLENS_V2_AUTH_ENABLED`
   is explicitly turned on.
2. **Jarvis's personality hasn't been deliberately written yet.** The
   "best friend, only thinks about your business" character is a real
   design target that likely isn't reflected yet in the actual prompting
   in `services/ai.py` / `services/specialist_orchestration.py` — treat
   this as an open, valuable piece of work, not something already done.
3. **"Beyond Pain" specificity.** Some code/data may implicitly assume this
   one clinic. Flag anything that hardcodes assumptions specific to
   Beyond Pain rather than being generic across clinic types.

## Recent history (for context, not action needed)

- The UI was rebuilt to match a "Mission Control" design (top bar, hero,
  metric cards, agent status panels, approval queue, activity feed).
- A bug causing Jarvis to fall back to templated/canned responses was
  fixed (silent error swallowing + a reasoning-model token budget issue).
- Storage was migrated from a hand-rolled JSON file to SQLite, then
  extended to optionally use Postgres (Supabase/Neon) via `DATABASE_URL`.
- The codebase was just cleaned up: ~130 dead files from earlier "Phase
  1–23" development (abandoned `sales/`, `finance/`, `hr/`, `marketing/`,
  `operations/`, `coo/`, `executive/`, `agents/` packages and their UI
  counterparts, none of which were ever actually wired into `app.py`) were
  removed. Only files reachable from `app.py` remain, plus a `tests/`
  folder with genuine regression tests for the live code. Two real bugs
  surfaced by those tests were fixed along the way (in
  `services/learning_memory_v22.py` and
  `services/agent_collaboration_v23.py`).

## V2 migration (in progress — read before touching `core/db/`, `core/identity/`, `alembic/`, `services/jarvis_memory.py`, `services/crm_read_router.py`, `services/tenant_operational_sync.py`, `services/integration_credentials.py`, `services/credential_encryption.py`, `integrations/*.py`)

LeadLens V2 is an incremental brownfield migration, not a rewrite.
**The legacy `core/memory.py` path remains the production source of
truth, and `core/auth.py` remains the production login gate, until an
explicit later phase changes either.** A relational SQLAlchemy/Alembic
schema exists (`core/db/`, `alembic/`, Phase 0), a real
identity/authorization backend is built on top of it (`core/identity/`,
Phase 1 — real users, Argon2id password hashing, organizations,
memberships, a 7-role/25-permission RBAC model, a 7-step `authorize()`
check), **Phase 2 made `services/jarvis_memory.py` — Jarvis's
learning-memory module — the first genuinely live consumer of this
schema** (reads/writes `core/db/models/jarvis.py`'s
`JarvisLearningRecord` table as its primary durable store, with the
legacy `data/learning/learning_memory.json` file kept permanently in
sync), **Phase 3 made `services/clinic_data_service.py` — every CRM
mutation — shadow-write into the Phase 0 relational CRM tables**
(`core/db/models/clinic.py`) via `services/relational_sync_service.py`,
behind `LEADLENS_V2_DUAL_WRITE_ENABLED` (**defaults OFF**), and **Phase
4 lets CRM reads route to the relational tables too, entity-by-entity**
via `services/crm_read_router.py`, behind 9 independent
`LEADLENS_V2_READ_<ENTITY>` flags (**all default OFF**) plus a
`LEADLENS_V2_READ_COMPARE` shadow-compare mode for pre-cutover
verification. The legacy `memory_store` write is always authoritative
(Phase 3 unchanged by Phase 4); a relational shadow-write failure is
caught, classified, and recorded (`core/db/models/shadow_sync.py`'s
`ShadowSyncFailure`) rather than ever rolling back or blocking the
legacy CRM operation, and a relational read mismatch/failure is
recorded the same way (`ReadMismatch`) — see
`docs/V2_PHASE3_CRM_DUAL_WRITE.md` and `docs/V2_PHASE4_READ_CUTOVER.md`.
**Phase 5 added `core/identity/tenant_context.py`** (an immutable
`TenantContext` — organization id plus actor identity, with USER/
SYSTEM/SCHEDULER/AUTOMATION actor types, deliberately no module-level
mutable global "current organization" state) **and
`services/tenant_operational_sync.py`**, which best-effort shadow-syncs
approvals, execution-queue items, security-audit events, and the
scheduler alert ledger into Phase 0's existing organization-scoped
relational operational tables, behind `LEADLENS_V2_TENANT_CONTEXT_ENABLED`
(**defaults OFF**) — hooked from `services/integration_manager_v21.py`,
`services/security_service.py`, and `scheduler/run_scheduled_checks.py`
the same shadow-sync-never-blocks-the-legacy-write way Phase 3 hooked
CRM mutations. Failure to resolve an organization for a tenant-owned
operation fails closed (no row written) — it never falls back to "the
first organization" or a global row. Jarvis, specialist orchestration,
and Jarvis memory were audited and found to already resolve exactly one
organization end-to-end via the existing Phase 2/4 mechanisms, so they
needed no code changes. See `docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md`
for the full tenant-flow audit, the Jarvis call-graph proof, and the
adversarial two-organization test suite
(`tests/test_phase5_tenant_context.py`). **Phase 6 added
`core/db/models/integration.py`'s `OrganizationIntegration`** (one
provider-neutral, organization-scoped row per WhatsApp/Gmail/Calendar
integration; secret fields Fernet-encrypted via
`services/credential_encryption.py` under a platform-level
`LEADLENS_CREDENTIAL_ENCRYPTION_KEY`, never stored in the DB) **and
`services/integration_credentials.py`** (the only module that
reads/writes that table — `configure_integration()`/
`disable_integration()`/`resolve_credentials()`, none of which accept a
caller-supplied organization id). `integrations/whatsapp_service.py`,
`gmail_service.py`, and `calendar_service.py` each gained one optional
`credentials` constructor parameter (their actual API-calling logic is
untouched); `services/integration_clients.py` is the factory layer that
turns a `TenantContext` into a configured adapter instance, used by
`services/integration_manager_v21.py` (the approval-gated queue) and
`services/appointment_messaging.py` (pre-authorized booking
confirmations/reminders) — both resolve the same transitional
`TenantContext` Phase 5 already established. A transitional
environment-variable fallback exists behind
`LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` (**defaults OFF**), and even
when enabled it only ever applies to the one transitional/default
organization — any other organization with an absent tenant credential
gets nothing, never another organization's or the deployment's env
credentials. `scripts/migrate_integration_credentials.py` (explicit,
dry-run-capable, idempotent, never overwrites an existing tenant
credential without `--force`, never prints a secret) and
`scripts/verify_integration_credentials.py` (safe status report, never
a secret value) support the migration. OpenAI/LLM credentials remain
platform-scoped, deliberately not made tenant-specific. **Phase 6.1**
(a focused hardening pass on top of Phase 6, no new files/migrations)
fixed three Phase 6 audit findings: `execute_item()` now derives its
`TenantContext` strictly from the queue item's own `organization_id`
(stamped by `prepare_execution()` at creation time, on both the
approval and the item — no fallback to the transitional default on a
missing/nonexistent/inactive organization, which fails execution
closed instead); `configure_integration()` no longer marks a fresh,
secret-less integration `ACTIVE`; `credential_encryption.redact()` is
documented as an intentionally-unwired opt-in utility rather than
wired into a contrived call site. **One real behavioral consequence to
know about**: any execution-queue item that existed *before* Phase 6.1
shipped has no `organization_id` and will now fail closed (`blocked`)
at `execute_item()` time rather than executing — acceptable for this
pre-production, single-clinic deployment, but worth knowing if old
"Approved" items are ever found stuck in the Action Center after this
deploys; re-preparing the action resolves it. See
`docs/V2_PHASE6_INTEGRATION_CREDENTIALS.md` for the full audit,
architecture, and adversarial test suite
(`tests/test_phase6_integration_credentials.py`). **Phase 7 — the
explicit, discussed phase the prior "do not wire authentication into a
live path" warning was waiting for — added a real per-user email/password
login** behind `LEADLENS_V2_AUTH_ENABLED` (**defaults OFF**):
`core/auth.py` now has two complete, independently-selected login
paths (legacy shared-password, unchanged byte-for-byte, vs. Phase 1's
`core.identity.authentication_service.authenticate()` against real
Users/Memberships/Organizations) — never both valid in one session.
`core/identity/session.py`'s `AuthenticatedSession` is revalidated
against the database on every access
(`core.auth.current_authenticated_session()`), so a user/membership/
organization disabled after login loses access within one call, not
indefinitely. The HMAC reload token that survives the Core switch's
forced browser navigation was preserved (re-keyed for V2, still only a
continuity convenience — restoring from it always re-triggers a full
database check, never trusts a claimed role/organization).
`services/authorization_guard.py`'s `require_permission()` is the one
RBAC chokepoint, wired into CRM mutations, approval decide/execute,
integration-credential administration, and member management; a no-op
whenever V2 auth is off or no live session exists, so scripts/tests/
scheduler actors are unaffected. `services/jarvis_tools.py` and
`services/specialist_orchestration.py` gate finance-sensitive Jarvis
tool output and specialist selection at the data boundary — this also
fixed a real pre-existing leak where `business_snapshot` exposed the
same financial data `revenue_summary` did, under a different tool name.
`services/member_management.py` provides minimal, backend-scoped
member administration (an org's admin can never touch another
organization's membership; the last active OWNER cannot be disabled or
demoted). See `docs/V2_PHASE7_AUTH_CUTOVER.md` for the full design,
the production runbook, and the adversarial test suite
(`tests/test_phase7_auth_cutover.py`). **Phase 7.1** (a focused
hardening pass on top of Phase 7, no new files/migrations beyond one new
test file) fixed three real defects an independent audit of Phase 7
found: a reload token minted just before logout could still silently
restore the session afterward, within its own 20-second TTL, because
`clear_session()` only ever cleared `st.session_state` and never touched
the `_auth` query param a browser-navigation rerun leaves behind — fixed
with defense in depth (`_render_logout_control_v2()` now explicitly
clears `st.query_params["_auth"]`, and `clear_session()` now stamps a
`logout_epoch()` that `_v2_reload_token_identity()` rejects tokens
minted at-or-before); `services/platform_data.py::save_company_profile()`
had no RBAC gate at all (any authenticated role could mutate
organization settings) — now requires `organization.manage`; and the
audit-log read path (`services/security_service.py::audit_rows()`, the
only live caller of which is `ui/phases_16_to_20.py`'s "Settings > Data
protection" tab) had no gate either — now requires `audit.view`. See
`docs/V2_PHASE7_AUTH_CUTOVER.md`'s Phase 7.1 addendum and
`tests/test_phase7_1_hardening.py`. **Phase 7.1.1** (a focused hardening
pass on top of Phase 7.1, no new files/migrations beyond one new test
file) fixed two defects an independent audit of Phase 7.1 found:
`onboarding.py` called the always-ungated `core.memory.save_company()`
directly instead of the RBAC-gated
`services.platform_data.save_company_profile()`, so any authenticated
V2-auth identity — not just one with `organization.manage` — could
initialize the company profile during the first-run window before
`core.memory.company_exists()` became True; fixed by routing onboarding
through `save_company_profile()` (legacy mode unaffected, since
`require_permission()` no-ops there). Separately, `_v2_reload_token()`'s
`int(time.time())` timestamp compared against
`core.identity.session.logout_epoch()`'s full-precision float could
wrongly reject a token minted in the same wall-clock second as a prior
logout; fixed by using full-precision timestamps on both sides
(`float()`-parsed, backward compatible with old integer-string tokens).
See `docs/V2_PHASE7_AUTH_CUTOVER.md`'s Phase 7.1.1 addendum and
`tests/test_phase7_1_1_hardening.py`. **Phase 8 — SaaS organization
onboarding + genuine two-organization validation** — the first phase to
make more than one organization actually *usable* in a live deployment,
not just representable in the schema. Its central finding: several
subsystems that looked tenant-ready from their Phase 0/5 relational
schema resolved organization via `resolve_default_organization_id()` —
the single transitional bootstrap org — *regardless of who was actually
logged in*, so a second organization's data would have silently landed
in (or read from) the first organization's rows. Fixed, each behind its
own independently-defaulted-OFF flag exactly like every prior phase:
`services/clinic_data_service.py`'s CRM reads AND writes (previously
writes only ever touched `core/memory.py`'s single global JSON list, so
two organizations could not even both use external id `"P-001"` — now,
behind `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED`, both read and
write route through `core/db/models/clinic.py`'s relational tables via
the live session's real organization, with `services/crm_tenant_writer.py`
as the new write path reusing `services/relational_sync_service.py`'s
existing per-entity mappers); `services/jarvis_memory.py` (behind
`LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED` — also stops a
non-default organization from ever falling back to the shared legacy
JSON file, which has always belonged to the default organization alone);
`services/tenant_operational_sync.py`'s audit-event shadow-sync (now
prefers the live session's organization when `LEADLENS_V2_TENANT_CONTEXT_ENABLED`
is on, previously always the transitional default regardless); and
company/clinic settings, which move off `core/memory.py`'s single global
`company` dict onto Phase 0's previously-dormant `OrganizationSettings`
table via new `core/identity/organization_profile_service.py`, behind
`LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED` — `app.py` now routes
onboarding through the new `services.platform_data.company_setup_complete()`,
which is organization-scoped under that flag instead of the old global
`core.memory.company_exists()`. All four resolvers share one new,
reused primitive: `core/identity/live_organization.py`'s
`resolve_live_organization_id()` (prefers the live authenticated
session's organization, falls back to the transitional default for
scripts/scheduler/no-session calls — never a caller-supplied id from
untrusted input). Also new: `scripts/provision_organization.py` (the
explicit, operator-only, idempotent CLI that creates an
Organization + initial OWNER User + Membership — deliberately separate
from the Phase 1 RBAC model; an OWNER of Clinic A cannot use it to
create Clinic B), `scripts/verify_multi_org_readiness.py` (a safe,
read-only multi-org status/isolation report, never patient-sensitive
data), an `OrganizationSettings.automations_enabled` column (new
organizations default to automations OFF), and
`scheduler/run_scheduled_checks.py::resolve_scheduler_organizations()`
now genuinely enumerates every ACTIVE organization with automations
explicitly enabled when `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` is on
(off: unchanged single-org behavior). **Phase 8.1** (a hardening pass
fixing two defects an independent Phase 8 audit found) closed both: a
live cross-tenant audit leak (`services/security_service.py`'s
`audit_rows()` — the actual "Settings > Data protection" reader — always
read the single legacy global `security_audit_log` section, so
Organization B could see Organization A's audit events despite the
relational `SecurityAuditEvent` shadow table already being correctly
organization-scoped; fixed behind a new, independent
`LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` flag, defaults OFF —
`audit_event()`'s write path is unchanged, only the read becomes
organization-scoped), and the scheduler execution-content gap just
described — every one of the 14 check functions (plus
`raise_owner_alert()`/`queue_patient_action()` and the idempotency
ledger) now accepts an optional `context: TenantContext | None = None`,
and `run_all_checks()` builds one `TenantContext` per enumerated
organization and runs every check once per organization with it when
multi-org mode is on (off: unchanged, every check still runs once with
`context=None`). Phase 8.1 also added a minimal, fail-open guard to
`marketing-site/api/lead.py` (refuses to write if more than one
organization exists in the target database) and reconfirmed — see
`docs/V2_PHASE8_SAAS_ONBOARDING.md`'s Phase 8.1 addendum — that this
endpoint remains genuinely unsupported for shared multi-org public lead
ingestion; not redesigned. One known remaining gap, explicitly
documented rather than silently fixed: `appointment_reminder()`'s 24hr
auto-send branch resolved its WhatsApp send implicitly, not
per-organization (closed in Phase 9 — see below). See that doc for the
full Phase 8 design, the second-clinic validation procedure,
`tests/test_phase8_saas_onboarding.py` (24 tests, including one
end-to-end two-organization scenario with deliberately overlapping
external ids), and `tests/test_phase8_1_hardening.py` (17 tests,
including an A→B→A scheduler-interleaving test and the exact cross-
tenant audit scenario the Phase 8 audit reproduced). **Phase 9 —
production hardening** (the final foundational migration phase; no
product redesign) closed the two remaining documented tenant-isolation
gaps and added the operational tooling a real production deployment
needs: `services/appointment_messaging.py`'s
`send_appointment_confirmation()`/`send_appointment_rsvp_reminder()` now
accept an optional `context: TenantContext | None`, threaded through
from `scheduler/run_scheduled_checks.py::appointment_reminder()`'s own
context, so the 24hr auto-send's patient lookup, clinic-name resolution,
and WhatsApp credential resolution are all organization-scoped;
`marketing-site/api/lead.py` now resolves exactly one trusted
organization via `LEADLENS_MARKETING_SITE_ORGANIZATION_SLUG` (or the
sole organization when only one exists) before every write, refusing
ambiguous multi-org writes, and best-effort shadow-writes into the
relational `leads` table. New: `core/config_validation.py` (centralized,
secret-free startup config validation), `core/observability.py`
(structured logging conventions — error categories, run ids, a
secret-redacting `log_event()`), `scripts/health_check.py`
(HEALTHY/DEGRADED/UNHEALTHY), `scripts/production_readiness.py` (the
single PASS/WARN/FAIL aggregator command), `scripts/backup_database.py`
+ `scripts/restore_validate.py` (backup/restore, reusing
`core.memory.backup_now()` rather than reimplementing it), and
extensions to `scripts/verify_multi_org_readiness.py` (membership
orphan check, shadow-sync health, an updated marketing-site readiness
finding). See `docs/V2_PHASE9_PRODUCTION_HARDENING.md` for the full
production-risk map, feature-flag classification, legacy `memory_store`
dependency map, and honest known-limitations list;
`docs/PRODUCTION_RUNBOOK.md` for the deployment/rollback procedures; and
`docs/BACKUP_RESTORE.md` for backup/restore specifically. Every other
live file — the approval/execution engine's actual decision logic, the
adapters' API-calling logic, the scheduler check functions' own
qualification/business logic (only their organization threading
changed, across Phase 8.1 and Phase 9) — is still completely untouched.
See `docs/V2_COEXISTENCE.md` for Phase 0's
architecture, `docs/V2_PHASE1_IDENTITY.md` for Phase 1's (including the
full role → permission matrix), `docs/V2_PHASE2_JARVIS_MEMORY.md` for
Phase 2's, `docs/V2_PHASE3_CRM_DUAL_WRITE.md` for Phase 3's (including
the full CRM mutation-surface audit and the known
`marketing-site/api/lead.py` bypass), `docs/V2_PHASE4_READ_CUTOVER.md`
for Phase 4's (including the full CRM read-path audit and the
entity-by-entity production activation runbook),
`docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md` for Phase 5's,
`docs/V2_PHASE6_INTEGRATION_CREDENTIALS.md` for Phase 6's (including the
full integration credential audit and the provider-by-provider
production migration procedure), `docs/V2_PHASE7_AUTH_CUTOVER.md`
for Phase 7's, and `docs/V2_PHASE8_SAAS_ONBOARDING.md` for Phase 8's
(including the platform-provisioning-vs-organization-RBAC distinction
and the full second-clinic manual validation procedure). Every phase's
rollback is the same shape — an env-var kill switch, no destructive DB
changes needed.

**Do not rebuild these without a real reason** (verified working,
tested, and recently hardened this session — see `docs/V2_COEXISTENCE.md`'s
own "Do not rebuild" section for the full reasoning per item):
specialist orchestration, the approval/execution engine, automation
qualification logic in `scheduler/run_scheduled_checks.py`, the
integration adapters' actual API-calling code in `integrations/*.py`,
the current Streamlit UI, the Docker foundation. Phase 1's original
"do not wire `core/identity/` into `core/auth.py`" restriction was
lifted by Phase 7, which was itself the explicit, discussed phase that
warning anticipated — done behind `LEADLENS_V2_AUTH_ENABLED`, legacy
path fully preserved. Phase 4 adds: do not enable any
`LEADLENS_V2_READ_<ENTITY>` flag in code (they must only be set via
deployment environment/secrets, per `docs/V2_PHASE4_READ_CUTOVER.md`'s
runbook), and do not turn off Phase 3's legacy-authoritative write path
— Phase 4 changes only which store answers a *read*. Phase 5 adds: do
not enable `LEADLENS_V2_TENANT_CONTEXT_ENABLED` in code (deployment
environment/secrets only), do not build a live organization switcher or
enforce new RBAC broadly in the UI. Phase 6 adds: do not enable
`LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` in code (deployment
environment/secrets only), do not run
`scripts/migrate_integration_credentials.py` automatically or on
startup, do not move `OPENAI_API_KEY`/LLM credentials into per-organization
storage, and do not build per-clinic OAuth consent flows or multi-account-
per-provider support — none of that is started. Phase 7 adds: do not
enable `LEADLENS_V2_AUTH_ENABLED` in code (deployment
environment/secrets only), do not build password-reset/email flows,
SSO, social login, a full SaaS organization switcher, or an invitation-
email system — none of that is started; do not remove the legacy
shared-password path from `core/auth.py` — it remains the rollback
target for as long as any deployment might need it. Phase 8 adds: do
not enable `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED`,
`LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED`,
`LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED`,
`LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED`, or (Phase 8.1)
`LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` in code (deployment
environment/secrets only, following every prior phase's rollout
discipline — see `docs/V2_PHASE8_SAAS_ONBOARDING.md`'s validation
procedure before ever turning these on against a real database); do not
run `scripts/provision_organization.py` automatically, on startup, or
from any Streamlit code path — it is an explicit, human-typed operator
command only; do not build public self-signup, billing, a broad
invitation-email system, or a full organization switcher UI — none of
that is started. Phase 8.1's scheduler-check `context` parameter and
`marketing-site/api/lead.py`'s ambiguous-multi-org guard were both
explicit, discussed, minimal changes (see
`docs/V2_PHASE8_SAAS_ONBOARDING.md`'s Phase 8.1 addendum) — do not
treat that as license to keep expanding either surface without a
similarly explicit ask: do not redesign the marketing-site public API
further, and do not thread organization context into
`services/appointment_messaging.py`'s auto-send credential resolution
(the one remaining documented gap) without discussing it first.

## Automation roadmap

See `docs/AUTOMATION_ROADMAP.md` for the phased build order for Jarvis's
autonomous automations (scheduler foundation, then risk-tiered rollout).
Follow it in order — do not start a later phase before the current one is
built and tested. If asked to "add an automation," check which phase it
belongs to and confirm with the founder before skipping ahead.

## Working style

- Confirm before big architectural decisions (especially anything
  touching multi-tenancy) rather than assuming and building.
- When something in the code contradicts the vision above, flag it rather
  than silently "fixing" it to match your best guess — the founder should
  decide, since some of these are product decisions, not bugs.
