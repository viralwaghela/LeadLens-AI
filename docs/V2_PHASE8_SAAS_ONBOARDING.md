# V2 Phase 8 — SaaS Organization Onboarding + Two-Organization Validation

## 1. Objective

Phases 0-7.1.1 built a relational multi-tenant schema, real identity/RBAC,
TenantContext, and per-organization integration credentials — but nothing
made a *second* organization actually usable. Phase 8's job: a minimal,
secure onboarding workflow for a new organization, and genuine proof that
two organizations can coexist in the same application process and the
same database, not just in isolated unit-test fixtures.

## 2. Audit of existing onboarding (section 1 classification)

| Component | Classification | Notes |
|---|---|---|
| `onboarding.py` (first-run wizard) | KEEP | Already routes through the gated `save_company_profile()` (Phase 7.1.1) — no change needed to the UI flow itself. |
| `app.py`'s `company_exists()` routing | **REFACTOR** | Was a single global check — the Phase 8 blocker. Replaced with `services.platform_data.company_setup_complete()`. |
| `services/platform_data.py::save_company_profile()` | REFACTOR | Now routes to organization-scoped storage when the new flag is on; unchanged otherwise. |
| Phase 1 `organization_service`/`user_service`/`membership_service` | KEEP | Exactly the CRUD Phase 8's provisioning CLI needed — no gaps found. |
| `scripts/bootstrap_identity.py`, `scripts/bootstrap_beyond_pain_org.py` | KEEP | Precedent for `scripts/provision_organization.py`'s shape (idempotent, explicit, never auto-run). |
| Phase 6 credential migration/configuration services | KEEP | Already fully organization-scoped (`services/integration_credentials.py`) — validated with a second organization, not modified. |
| `services/clinic_data_service.py` (CRM) | **REPLACE (write path), REFACTOR (read path)** | The core finding — see §3. |
| `services/jarvis_memory.py` | REFACTOR | Same class of gap as CRM, smaller fix. |
| `services/tenant_operational_sync.py` (audit shadow-sync) | REFACTOR | Same class of gap, smaller fix. |
| `scheduler/run_scheduled_checks.py` | REFACTOR (enumeration only) | See §9 — execution content is DEFERRED. |
| Hard-coded Beyond Pain assumptions | none found in code touched this phase | `scripts/bootstrap_beyond_pain_org.py`'s hard-coded slug/name is itself explicitly a one-off, human-run script, not app logic. |

## 3. The core finding: "tenant-ready schema" ≠ "tenant-aware live code"

`core/db/models/clinic.py`'s relational CRM tables have been organization-
scoped since Phase 0. But the *live write path* — `services/clinic_data_service.py`'s
`add_record()`/`update_record()` — wrote only to `core/memory.py`'s single
global JSON list, with no `organization_id` on the rows at all. Two
organizations could not both create a patient with external id `"P-001"`
— the uniqueness check ran across the whole global list. Phase 3/4's
relational tables were a *shadow copy* of that global list, resolved via
`resolve_default_organization_id()` — the single transitional bootstrap
organization — never the actual logged-in organization.

The same shape of bug existed in three more places, found during this
phase's own implementation and fixed the same way:

- `services/jarvis_memory.py` resolved its organization via
  `resolve_default_organization_id()` unconditionally — plus its legacy
  JSON-file fallback/mirror is itself a single shared file, so even a
  DB-only fix would have leaked through the file fallback for a
  non-default organization.
- `services/tenant_operational_sync.py::_resolve_context()` (used by
  `audit_event()`'s shadow-sync, among others) had the identical bug.
- `services/platform_data.py`'s company profile was `core/memory.py`'s
  single global `company` dict, with no organization concept at all.

## 4. The fix: one shared resolver, four independent flags

`core/identity/live_organization.py::resolve_live_organization_id(session)`
is the new shared primitive: prefers the current live authenticated V2
session's organization (`core.auth.current_authenticated_session()`),
falling back to the transitional default organization for scripts, the
scheduler, and any call with no live session — exactly the same fallback
discipline Phase 2-5 already established. No caching, no module-level
mutable "current organization" — resolved fresh every call, matching
`core/identity/tenant_context.py`'s own explicit design choice.

Four independent flags gate its use, each defaulting OFF, each an
explicit, separately-reviewable kill switch (never implied by another
flag, following every prior phase's convention):

| Flag | Gates | New/changed module |
|---|---|---|
| `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED` | CRM reads AND writes become organization-scoped, relational-authoritative for all 9 entities | `services/crm_read_router.py`, new `services/crm_tenant_writer.py`, `services/clinic_data_service.py` |
| `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED` | Company/clinic settings move to `OrganizationSettings` per organization | new `core/identity/organization_profile_service.py`, `services/platform_data.py` |
| `LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED` | Jarvis learning memory resolves per-organization; a non-default org never touches the shared legacy JSON file | `services/jarvis_memory.py` |
| `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` | Scheduler enumerates every ACTIVE + automations-enabled organization instead of always the single transitional one | `scheduler/run_scheduled_checks.py` |

`LEADLENS_V2_TENANT_CONTEXT_ENABLED` (Phase 5, already existing) now
*also* means "prefer the live session's organization" for
`tenant_operational_sync._resolve_context()` — previously it only meant
"attempt the shadow write at all." Since it has never been enabled in
production, this is a safe refinement, not a behavior change for any
live deployment.

None of these four flags are enabled anywhere in code — only
`tests/test_phase8_saas_onboarding.py`'s own isolated fixture turns them
on, via `monkeypatch`, matching the fixture pattern every prior phase's
tests already use. **Do not enable any of them outside deployment
environment/secrets** — see §12.

## 5. Organization creation — `scripts/provision_organization.py`

A trusted, explicit, operator-only CLI, kept deliberately separate from
the Phase 1 organization RBAC model (§13 below explains why). Idempotent
at every step:

- get-or-create Organization by slug (never a duplicate).
- get-or-create the initial OWNER User by email — an existing user's
  password is **never** touched or overwritten; `--existing-user`
  attaches an already-existing user without any password prompt at all.
- get-or-create an ACTIVE OWNER Membership.

No secret is ever printed or logged (`--owner-password-env` for
non-interactive use, or a hidden `getpass` prompt). New organizations
get `OrganizationSettings.automations_enabled = False` by default (no
row exists yet at all until the first onboarding/settings save) — see
§9.

## 6. Platform provisioning vs. organization RBAC — the boundary

An OWNER of Clinic A has `organization.manage`, `members.manage`, etc.
— all scoped to Clinic A's `organization_id` via the Phase 1 permission
model. None of that grants the ability to create Clinic B: nothing in
the live Streamlit app calls `organization_service.create_organization()`
at all. The only way a new Organization row is created is a human with
shell access to the deployment running `scripts/provision_organization.py`
directly — a platform-operator capability, not a member permission, and
intentionally not modeled as a Phase 1 permission at all (so it can
never be granted or escalated to through the RBAC UI). This is the
smallest secure mechanism that supports real second-clinic testing
without building self-service signup (explicitly out of scope — §14).

## 7. Company/organization profile

`services/platform_data.py::company_setup_complete()` is the new
onboarding-routing check `app.py` calls instead of
`core.memory.company_exists()`. When `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED`
is on and a live organization resolves, it checks *that organization's*
`OrganizationSettings` row (previously dormant Phase 0 infrastructure —
"nothing in the live app creates, reads, or references this table yet").
`save_company_profile()` and `business_snapshot()`'s `company` field
follow the same rule. Off, or with no live session (scripts, legacy
mode): unchanged, `core/memory.py`'s global `company` dict, byte-identical
to pre-Phase-8 behavior.

## 8. CRM initial state and overlapping external IDs

With `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED` on, a freshly
provisioned organization's `list_records()` calls return `[]` for every
entity — there is no shared global list to inherit from. Two
organizations can both create patient `"P-001"`: external-id numbering
(`services/crm_tenant_writer.py::_next_external_id()`) is computed only
from that organization's own relational rows. Validated directly in
`tests/test_phase8_saas_onboarding.py::test_crm_overlapping_external_ids_coexist_and_isolate`
and the end-to-end test.

Programmatic callers (scheduler, tests, provisioning verification) may
pass `organization_id=` explicitly to `list_records()`/`get_record()`/
`add_record()`/`update_record()`/`search_records()` — a keyword-only,
default-`None` escape hatch (mirroring `services/crm_read_router.py::read_rows()`'s
own `organization_id` parameter). The live Streamlit UI never passes it,
relying entirely on the implicit, session-based resolution.

## 9. Scheduler: enumeration is real, per-org execution content is not

`scheduler/run_scheduled_checks.py::resolve_scheduler_organizations()`,
when `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` is on, genuinely queries
every `Organization` with `status=ACTIVE` joined to an
`OrganizationSettings` row with `automations_enabled=True`. A brand-new
organization has no `OrganizationSettings` row at all until its first
settings save, so it is correctly excluded until an operator explicitly
both completes onboarding and turns automations on.

**Explicitly deferred, not silently implied as solved**: the 14 check
functions in `CHECKS` are still zero-argument and read/write CRM data
via the same implicit, session-based `resolve_live_organization_id()`
every other live caller uses — there is no live Streamlit session during
a scheduler run, so they always fall back to the transitional default
organization regardless of what `resolve_scheduler_organizations()`
enumerates. Looping each check function once per enumerated organization
against *that* organization's own data is real, non-trivial work (each
function's internal CRM/messaging/approval calls would need explicit
`organization_id`/`TenantContext` threading) — tracked as technical debt,
not started this phase. Do not assume enumeration implies execution.

## 10. Organization DISABLED — already enforced end-to-end

No new code was needed here — Phase 5/6.1/7's existing boundaries already
cover it, and Phase 8 only adds one test proving the whole chain:
`core.auth.current_authenticated_session()` fails closed and clears the
session the moment `Organization.status` flips to `INACTIVE`;
`resolve_scheduler_organizations()` excludes non-ACTIVE organizations by
construction; `execute_item()` (Phase 6.1) already derives its
`TenantContext` from the item's own stamped `organization_id` and would
fail the same way; `resolve_credentials()` (Phase 6) resolves per
organization and is unaffected by a *different* organization's status.

## 11. Second-clinic realistic fixture

`tests/test_phase8_saas_onboarding.py` provisions two full organizations
per relevant test — OWNER (+ a RECEPTIONIST/VIEWER in some tests),
company settings, patients/leads with deliberately overlapping external
IDs, WhatsApp integration credentials, a prepared approval/execution-queue
action, Jarvis memory, and audit events — then asserts isolation in both
directions. The one end-to-end test
(`test_end_to_end_two_organization_validation`) runs the full provision
→ login A → create/read A data → Jarvis A → prepare A action → logout →
login B → confirm A data absent → create/read B data (same external ID)
→ Jarvis B → prepare an identical B action → verify separate
approvals/credentials/audit → re-login A → confirm A unchanged sequence
from the Phase 8 spec, entirely within one shared SQLite database and
one Python process — never two different `DATABASE_URL`s.

## 12. Production validation procedure (safe second-clinic test)

1. Deploy Phase 8's code with all four new flags left OFF (the default)
   — confirm the existing deployment behaves exactly as before.
2. In a **non-production** environment first, turn on
   `LEADLENS_V2_AUTH_ENABLED`, `LEADLENS_V2_TENANT_CONTEXT_ENABLED`,
   `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED`,
   `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED`,
   `LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED`. Leave
   `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` OFF for now (§9 — no
   outbound actions should be automatic yet).
3. `python scripts/provision_organization.py --organization-name "Test
   Clinic B" --slug test-clinic-b --owner-email owner@test-clinic-b.example`
   (interactive password prompt, or `--owner-password-env`).
4. Log in as Owner B. Confirm the onboarding screen appears (empty
   workspace) — not Clinic A's dashboard.
5. Complete onboarding (creates Clinic B's `OrganizationSettings` row).
6. Create one test patient, one appointment, one lead — confirm they use
   external ids independent of Clinic A's numbering (e.g. both can be
   `P-001`).
7. Log in as Owner A in a separate session/browser — confirm Clinic A's
   data, settings, and Jarvis memory are completely unaffected and
   Clinic B's test data is not visible.
8. Log back in as Owner B — confirm Clinic A's data is not visible.
9. Ask Jarvis a question as Owner B — confirm the response reflects only
   Clinic B's (empty/minimal) context.
10. Configure a **test/sandbox** WhatsApp/Gmail/Calendar credential for
    Clinic B via `services/integration_credentials.configure_integration()`
    (backend/script — no UI built this phase, per §14) or leave
    UNCONFIGURED. Confirm `resolve_credentials()` for Clinic B never
    returns Clinic A's credential.
11. Prepare one dry-run approval/action for Clinic B
    (`services/integration_manager_v21.prepare_execution()`) — confirm it
    creates an independent queue item/approval, never touching Clinic A's.
12. Create a RECEPTIONIST (or other restricted role) user under Clinic B
    — confirm Phase 7 RBAC denies/allows exactly per the Phase 1 matrix,
    same as Clinic A.
13. `python scripts/verify_multi_org_readiness.py` — confirm counts and
    the cross-org FK check are as expected for both organizations.
14. Re-login Owner A one more time — confirm nothing changed.
15. Only once all of the above pass: consider enabling
    `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` for Clinic B specifically
    (via its `OrganizationSettings.automations_enabled`), fully aware of
    §9's execution-content limitation — outbound automations will still
    run against the transitional default organization's data during a
    scheduler pass, not Clinic B's, until that technical debt is
    resolved. Do not rely on the scheduler for real Clinic B automations
    yet.
16. Only after a full, deliberate review — including the technical debt
    in §9 — consider onboarding a real, paying second clinic.

## 13. Do not implement (confirmed untouched this phase)

Public self-signup, paid subscriptions/billing/Stripe/Razorpay, a large
invitation-email system, SSO, a broad password-recovery system, UI
redesign, React, removal of legacy `memory_store`, removal of Phase 3
dual-write, removal of Phase 4 flags, any destructive cleanup, Phase 9
observability/backup work.

## 14. Do not rebuild

Specialist orchestration, the approval/execution engine's actual
decision logic, the integration adapters' API-calling code, the current
Streamlit UI, the scheduler's 14 check functions' own qualification
logic (only their organization-*eligibility* enumeration changed) — all
reused, none rewritten. See `docs/V2_COEXISTENCE.md`'s own "Do not
rebuild" section and `CLAUDE.md`'s Phase 8 addendum for the full list of
new kill switches this phase adds.

## 15. Phase 8.1 addendum — audit isolation + scheduler execution context

An independent audit of Phase 8 (AUDIT ONLY) found two real defects and
one previously-undocumented limitation, all addressed here.

### 15.1 Confirmed defect: live cross-tenant audit leak

`services/security_service.py::audit_event()` always wrote (and
`audit_rows()` always read) the single legacy global
`security_audit_log` section — the relational `SecurityAuditEvent`
shadow table was already correctly organization-scoped, but the actual
human-facing reader behind Settings > Data protection never used it.
Reproduced directly: Organization B, with valid `audit.view`, could see
Organization A's audit events.

**Fixed**: a new flag, `LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED`
(defaults OFF, independent of every other Phase 8 flag), makes
`audit_rows()` read exclusively from `SecurityAuditEvent` filtered to
the live session's own `organization_id` when on and a live organization
resolves. `audit_event()`'s write path is unchanged — it still always
writes the legacy global section unconditionally (this is the documented
legacy-compatibility path: turning the new flag off, or running with no
live session at all, still produces a working, if global, audit trail
for scripts/system callers and any deployment that hasn't opted in) —
only the *read* becomes organization-scoped, and only when explicitly
enabled.

### 15.2 Confirmed limitation: scheduler execution was enumeration-only

Phase 8's `resolve_scheduler_organizations()` could correctly enumerate
eligible organizations, but the 14 check functions in `CHECKS` were
still zero-argument and resolved CRM data implicitly — during a real
scheduler run there is no live Streamlit session, so they always fell
back to the transitional default organization regardless of which
organization enumeration identified.

**Fixed**: every one of the 14 check functions (plus the two shared
action helpers, `raise_owner_alert()`/`queue_patient_action()`, and the
idempotency ledger, `already_flagged()`/`_mark_flagged()`) now accepts
an optional, keyword-compatible `context: TenantContext | None = None`
parameter. `run_all_checks()`, when `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED`
is on, builds one `TenantContext` per enumerated organization
(`actor_type=SCHEDULER`, no fake human identity — see
`core.identity.tenant_context.build_system_context()`) and runs every
check once per organization with that context; every CRM read inside a
check is threaded with `organization_id=context.organization_id`, every
generated approval/execution-queue item is stamped with that
organization via `prepare_execution(tenant_context=context)`, and
idempotency-ledger keys are organization-scoped
(`services.clinic_data_service.py`'s `patient_profile()`/
`patient_risk_summary()`/`clinic_metrics()`/`records_with_patient_names()`
and `services.live_workflow_service.py`'s `due_followups()` also gained
the same `organization_id=` threading, since `monthly_business_review`
and `inactive_patient_recovery` depend on them). Off (the default):
`run_all_checks()` calls every check with `context=None`, which resolves
everything via the same implicit, transitional-default-organization path
every pre-Phase-8.1 call used — byte-identical behavior, verified by the
full pre-existing suite staying green.

**Known remaining limitation, explicitly not fixed this phase**: the
24-hour auto-send branch of `appointment_reminder()`
(`services/appointment_messaging.py::send_appointment_rsvp_reminder()`)
is not yet organization-context aware — its patient lookup, clinic-name
resolution, and WhatsApp credential resolution are all still implicit.
The CRM reads immediately around it in `appointment_reminder()` are
correctly org-scoped, but the actual send still resolves via the
transitional default organization. Threading `appointment_messaging.py`
(and its own credential-resolution call chain) through an explicit
context is a reasonably-sized follow-up, deliberately deferred rather
than folded into this already-large refactor.

### 15.3 Marketing-site boundary — reconfirmed, minimal guard added

`marketing-site/api/lead.py` remains a separately-deployed function with
no organization concept, writing via raw `psycopg2` SQL directly into
`memory_store` id=1. Not redesigned this phase (explicitly out of
scope). One minimal, low-risk guard was added:
`_reject_if_ambiguous_multi_org()` queries `SELECT count(*) FROM
organizations` before every insert and refuses to write (returning the
same generic "couldn't save your enquiry" error the visitor already
sees for any failure — no internal detail leaked) if more than one
organization exists in the target database. This fails **open** if the
check itself errors (a data-hygiene guard, not an authorization
boundary) so it can never break an existing single-clinic deployment's
working booking form. **Conclusion, stated plainly: shared multi-org
deployment + this existing endpoint = UNSUPPORTED for public lead
ingestion** until the endpoint becomes genuinely organization-aware (out
of scope for any phase so far).

### 15.4 Readiness verifier — no false PASS

`scripts/verify_multi_org_readiness.py` gained
`multi_org_readiness_gate()`, printed as a "Multi-org production
readiness gate" section and reflected in the script's exit code (1 on
any FAIL): FAILs when more than one organization exists and audit reads
are still global; WARNs (never silently passes) about the
marketing-site limitation whenever more than one organization exists;
WARNs if scheduler multi-org mode is available but not enabled.

### 15.5 New flags summary (all default OFF)

| Flag | Gates |
|---|---|
| `LEADLENS_V2_AUDIT_TENANT_AUTHORITATIVE_ENABLED` | `audit_rows()` reads become organization-scoped |
| `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED` | (Phase 8, reused) scheduler enumerates + now genuinely executes per-organization |

See `tests/test_phase8_1_hardening.py` (17 tests) for verification,
including the exact cross-tenant audit scenario the audit reproduced and
an A→B→A scheduler interleaving test with deliberately overlapping IDs.
