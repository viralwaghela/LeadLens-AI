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
   0 through 6 have been built** (a relational schema, an identity/
   authorization backend on top of it, Jarvis's learning-memory storage
   now genuinely running through this schema, CRM writes shadow-writing
   into the relational schema, CRM *reads* that can be routed to the
   relational schema too, entity-by-entity, behind flags that all
   currently default OFF, a `TenantContext` abstraction plus
   tenant-scoped shadow copies of approvals, the execution queue, audit
   events, and the scheduler ledger, all behind
   `LEADLENS_V2_TENANT_CONTEXT_ENABLED` (**defaults OFF**), and — Phase
   6 — encrypted, organization-scoped WhatsApp/Gmail/Calendar
   credentials with a transitional environment-variable fallback, behind
   `LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` (**defaults OFF**) — see
   "V2 migration" below) but Jarvis, the scheduler, approvals, and
   `core/auth.py`'s shared-password login are all still exclusively
   legacy and single-tenant in live operation, CRM reads remain legacy
   in every live deployment until an operator explicitly opts an entity
   in, and every integration adapter still reads its deployment-wide
   environment credentials directly until an operator explicitly
   migrates an organization's credentials into the database; none of
   these phases change the multi-tenant gap itself — there is still no
   live tenant-routing cutover, no organization switcher, and no live
   RBAC enforcement — they only lay groundwork for the phase that
   eventually will.
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
platform-scoped, deliberately not made tenant-specific. See
`docs/V2_PHASE6_INTEGRATION_CREDENTIALS.md` for the full audit,
architecture, and adversarial test suite
(`tests/test_phase6_integration_credentials.py`). Every other live
file — `app.py`, `dashboard.py`, `core/auth.py`, the scheduler's check
functions themselves, the approval/execution engine's actual decision
logic, the adapters' API-calling logic — is still completely untouched.
**Do not wire multi-tenant organization switching or authentication
into a live path without that being its own explicit, discussed
phase** — see `docs/V2_COEXISTENCE.md` for Phase 0's architecture,
`docs/V2_PHASE1_IDENTITY.md` for Phase 1's (including the full role →
permission matrix), `docs/V2_PHASE2_JARVIS_MEMORY.md` for Phase 2's,
`docs/V2_PHASE3_CRM_DUAL_WRITE.md` for Phase 3's (including the full
CRM mutation-surface audit and the known `marketing-site/api/lead.py`
bypass), `docs/V2_PHASE4_READ_CUTOVER.md` for Phase 4's (including the
full CRM read-path audit and the entity-by-entity production activation
runbook), `docs/V2_PHASE5_TENANT_BUSINESS_LOGIC.md` for Phase 5's, and
`docs/V2_PHASE6_INTEGRATION_CREDENTIALS.md` for Phase 6's (including the
full integration credential audit and the provider-by-provider
production migration procedure). Every phase's rollback is the same
shape — an env-var kill switch, no destructive DB changes needed.

**Do not rebuild these without a real reason** (verified working,
tested, and recently hardened this session — see `docs/V2_COEXISTENCE.md`'s
own "Do not rebuild" section for the full reasoning per item):
specialist orchestration, the approval/execution engine, automation
qualification logic in `scheduler/run_scheduled_checks.py`, the
integration adapters' actual API-calling code in `integrations/*.py`,
the current Streamlit UI, the Docker foundation. Phase 1 adds: do not
wire `core/identity/` into `core/auth.py` or any UI login form without
that being its own explicit, discussed phase. Phase 4 adds: do not
enable any `LEADLENS_V2_READ_<ENTITY>` flag in code (they must only be
set via deployment environment/secrets, per
`docs/V2_PHASE4_READ_CUTOVER.md`'s runbook), and do not turn off Phase
3's legacy-authoritative write path — Phase 4 changes only which store
answers a *read*. Phase 5 adds: do not enable
`LEADLENS_V2_TENANT_CONTEXT_ENABLED` in code (deployment
environment/secrets only), do not build a live organization switcher or
enforce new RBAC broadly in the UI. Phase 6 adds: do not enable
`LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` in code (deployment
environment/secrets only), do not run
`scripts/migrate_integration_credentials.py` automatically or on
startup, do not move `OPENAI_API_KEY`/LLM credentials into per-organization
storage, and do not build per-clinic OAuth consent flows or multi-account-
per-provider support — none of that is started.

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
