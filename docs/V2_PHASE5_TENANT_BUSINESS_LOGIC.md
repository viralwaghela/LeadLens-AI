# V2 Phase 5 — tenant-scoped business logic

Read this before touching `core/identity/tenant_context.py`,
`services/tenant_operational_sync.py`, or any of their call sites
(`services/security_service.py`, `services/integration_manager_v21.py`,
`scheduler/run_scheduled_checks.py`). Read `docs/V2_COEXISTENCE.md`,
`docs/V2_PHASE1_IDENTITY.md`, `docs/V2_PHASE2_JARVIS_MEMORY.md`,
`docs/V2_PHASE3_CRM_DUAL_WRITE.md`, and `docs/V2_PHASE4_READ_CUTOVER.md`
first — this document assumes those rules still apply. **Phase 5 does
not replace live authentication, does not activate live RBAC, and does
not change any live user-facing behavior.** It makes the business-logic
layer tenant-*aware* underneath the still-single-clinic live surface.

## 0. Tenant-flow audit (performed before writing any Phase 5 code)

Traced every `load_memory()`/`update_memory()` caller in the repo and
classified each:

| Component | Classification | Notes |
|---|---|---|
| CRM reads/writes (`services/clinic_data_service.py`) | **A** — already organization-scoped | Phase 3 (writes) + Phase 4 (reads), unchanged by Phase 5 |
| Jarvis learning memory (`services/jarvis_memory.py`) | **A** — already organization-scoped | Phase 2, unchanged by Phase 5 |
| Jarvis context/tools/specialists/agent router | **A** — already organization-scoped, transitively | See §4 below — traced the full call graph, zero independent CRM reads found |
| Approvals + execution queue (`services/integration_manager_v21.py`) | **B → fixed** | No organization scoping existed before Phase 5; now shadow-synced, org-scoped, via `tenant_operational_sync.py` |
| Security/audit log (`services/security_service.py`) | **B → fixed** | Same — `audit_event()` now shadow-syncs an org-scoped `SecurityAuditEvent` row |
| Scheduler idempotency ledger (`already_flagged`/`_mark_flagged`) | **B → fixed** | Same — now shadow-syncs an org-scoped `SchedulerAlertLedgerEntry` row |
| Scheduler's 14 `@check` functions | **A** (unchanged, by design) | Each is zero-argument and reads/writes only through `clinic_data_service`, `raise_owner_alert`, and `queue_patient_action` — all already covered by the fixes above; no per-check changes needed or made |
| `services/platform_data.py` (tasks/decisions/company/reports) | **C** — global by design | Business-operational metadata, not tenant CRM data; explicitly out of scope per Phase 5's own "do not migrate unrelated/global application metadata" instruction |
| `ui/jarvis_center.py` and its engine files (`briefing_engine.py`, `campaign_engine.py`, `chief_of_staff_agents.py`, `execution_engine.py`, `integration_hub.py`, `memory_engine.py`, `monitoring_engine.py`) | **D** — dead/unreachable | Confirmed in the Phase 4 audit and reconfirmed here: zero live importers, unreachable from `app.py`. Not touched. |
| `marketing-site/api/lead.py` | **E** — deferred, documented | Separate deployment, raw-SQL write path, predates any organization concept. See §12. |
| Per-clinic integration credentials | **E** — deferred to Phase 6 | See §11. |
| Live Streamlit login / Phase 1 RBAC activation | **E** — deferred to the explicit auth-cutover phase | See §9/§10. |

## 1. TenantContext — `core/identity/tenant_context.py`

```python
@dataclass(frozen=True)
class TenantContext:
    organization_id: int
    actor_type: ActorType          # USER | SYSTEM | SCHEDULER | AUTOMATION
    user_id: int | None = None
    membership_id: int | None = None
    role: MembershipRole | None = None
    permissions: frozenset[str] = frozenset()
    source: str = ""
```

Immutable (frozen dataclass — `test_tenant_context_is_frozen` proves
mutation raises). **No module-level mutable state anywhere in this
file** — every function takes a `Session` and returns a fresh context;
there is no `CURRENT_ORGANIZATION` global to leak across requests,
threads, or scheduler runs (`test_no_module_level_mutable_tenant_state`
checks for this pattern directly).

Four constructors, each fails closed:

- `build_user_context(session, user_id=, organization_id=)` — delegates
  entirely to `core.identity.authorization_service.resolve_identity()`
  (Phase 1's own tested 6-step check: user active → org active →
  membership active). Returns `None` on any failure — never fabricates
  a context from unvalidated input. **Not called by any live path
  yet** — see §9.
- `build_system_context(session, organization_id=, actor_type=)` — for
  scheduler/automation jobs. Refuses `ActorType.USER` (raises
  `ValueError`) since a system job is never a human. No user/membership
  fields are ever populated for non-USER actors — Phase 5 does not
  force a fake user onto scheduler/system jobs, per its own explicit
  instruction.
- `resolve_transitional_organization_id(session)` — a thin, deliberately
  trivial wrapper around
  `core.identity.default_organization.resolve_default_organization_id()`
  — the exact mechanism Phase 2/3/4 already use and proved safe
  (get-or-create by `LEADLENS_DEFAULT_ORG_SLUG`, resolved fresh, never
  cached, never accepting caller input). Kept as its own named function
  so a future phase that removes the transitional bridge has one call
  path to change, not several.
- `build_transitional_context(session, actor_type=)` — what an existing
  single-clinic deployment resolves today, with live authentication not
  yet replaced. This is Phase 5's explicit, permitted bridge (§2 of the
  task spec) — deterministic, non-user-injectable (the organization
  always comes from `resolve_transitional_organization_id()`, never a
  parameter a caller could substitute).

## 2. Why `jarvis_context.py`/`jarvis_memory.py`/specialists were not modified

Traced the full call graph: `services/specialist_orchestration.py` only
consumes `services/jarvis_context.py::build_jarvis_context()` and
`services/jarvis_tools.py::run_read_only_tool()`; `jarvis_tools.py` has
**zero** direct `load_memory`/`list_records`/`clinic_data_service`
references (everything it returns is derived from the context it's
handed); `services/agent_router.py` does no data access at all (pure
routing); `services/business_jarvis_engine.py` imports `load_memory`
but never calls it (a dead import). Every actual CRM/Jarvis-memory read
in this whole stack goes through `clinic_data_service.list_records()`
(Phase 4-routed, already organization-scoped) or `jarvis_memory.py`
directly (Phase 2, already organization-scoped) — both already resolve
the exact same single trusted organization
`tenant_context.py` now formalizes.

**Jarvis therefore already operates inside exactly one TenantContext
today**, by construction, not by accident — Phase 5 adds no code to
these files because adding an unused `context` parameter that every
call site would immediately discard (there being only one resolvable
organization) would be exactly the kind of unrelated, risk-for-no-gain
refactor both this phase and the project's general engineering guidance
warn against. `tests/test_phase5_tenant_context.py::test_jarvis_context_only_contains_resolved_organization_patients`
and `::test_jarvis_memory_isolated_across_organizations` prove the
isolation property directly at the data layer (not by inspecting prompt
text) — a second organization's patient/preference, inserted directly
at the relational layer with an overlapping business identifier, never
appears in what Jarvis actually builds/reads.

## 3. Operational tenancy — `services/tenant_operational_sync.py`

Same authoritative-write contract as Phase 3: the legacy write already
succeeded by the time any function here runs; this is a best-effort,
**shadow** relational copy, never authoritative, never blocking or
rolling back the legacy operation. Uses Phase 0's own
`core/db/models/operations.py` models — `Approval`, `ExecutionQueueItem`,
`SecurityAuditEvent`, `SchedulerAlertLedgerEntry` — which already had
correct organization-scoped composite uniqueness from Phase 0 (no new
Alembic migration was needed for Phase 5 — confirmed by a clean
`alembic check` against the unchanged schema).

Kill switch: `LEADLENS_V2_TENANT_CONTEXT_ENABLED`, **defaults OFF**.
Three live hook points, chosen for the same minimal-surface-area reason
Phase 3 hooked exactly two functions for all CRM writes:

| Hook | File | Covers |
|---|---|---|
| `audit_event()` | `services/security_service.py` | Every audit log write in the app (20+ call sites across `ui/patient_crm.py`, `ui/jarvis_mode.py`, `integration_manager_v21.py`) |
| `prepare_execution()` / `decide_item()` / `execute_item()` | `services/integration_manager_v21.py` | Every approval + execution-queue create/approve/reject/execute |
| `_mark_flagged()` | `scheduler/run_scheduled_checks.py` | Every one of the 14 registered checks' idempotency writes, since `raise_owner_alert()`/`queue_patient_action()` both call it |

### Approval fingerprint collisions across organizations (§15)

`Approval`/`ExecutionQueueItem` already carry
`UniqueConstraint(organization_id, external_id)` from Phase 0 —
confirmed and proven directly:
`test_approval_and_item_external_ids_isolated_across_organizations`
creates two approvals with the identical external ID under two
different organizations and confirms both persist independently, with
distinct titles retrievable. `SchedulerAlertLedgerEntry`'s
`UniqueConstraint(organization_id, check_name, item_key)` gets the same
treatment in `test_scheduler_ledger_entries_are_organization_scoped` —
an identical `(check_name, item_key)` pair under two organizations
produces two ledger rows, and marking one organization's event does not
suppress the other's (`test_scheduler_ledger_idempotent_within_one_organization`
proves the *same*-organization case still correctly dedupes).

## 4. Scheduler organization resolution — `resolve_scheduler_organizations()`

```python
def resolve_scheduler_organizations() -> list[int]:
    ...  # today: always exactly one element
```

Added at the top of `run_all_checks()`. Resolves once per scheduler
run (not once per check, not once per organization inside a loop —
`test_scheduler_org_resolution_returns_list` and the "no hidden N+1"
requirement are both satisfied by construction: one call, one list).
Returns an **empty list** — never a fallback, never a guess — if
resolution itself fails
(`test_scheduler_org_resolution_fails_closed_on_db_error`), logged as
`scheduler_org_scope_failure`.

**The 14 `@check` functions themselves are unchanged and remain
zero-argument.** A single-element loop over them is behaviorally
identical to not looping (proven: `test_run_all_checks_still_works_end_to_end`
confirms all 14 still run with zero failures). This is deliberate,
matching the task's own explicit instruction: "Do NOT create
uncontrolled multi-clinic production execution yet if current
deployment still only has one configured organization. Architecture
must support multiple organizations safely." The architecture now
supports it (one call site would need to change — enumerate real
organizations instead of resolving the transitional one, and thread a
`TenantContext` into each check — when that becomes a real, discussed
need); today's behavior does not change at all.

## 5. `read_services()` tightening (§30 — a real, if minor, finding)

Found in the Phase 4 audit: `crm_read_router.read_services()` accepted
an optional caller-supplied `organization_id`, inconsistent with every
other relational reader in that module (none of which ever accept
one). Harmless in practice — nothing live called it with untrusted
input, since nothing live called it at all (it remains dormant, no
legacy `services` entity exists to route from). Fixed here: the
parameter is removed entirely; organization is now always resolved via
the same trusted `resolve_default_organization_id()` every other reader
uses. `test_read_services_accepts_no_organization_parameter` and
`test_read_services_resolves_via_trusted_context` cover this directly.

## 6. Dead code (§29 — confirmed, not touched)

`ui/jarvis_center.py` and the engine files that only it imports
(`briefing_engine.py`, `campaign_engine.py`, `chief_of_staff_agents.py`,
`execution_engine.py`, `integration_hub.py`, `memory_engine.py`,
`monitoring_engine.py`) are unreachable from `app.py` — reconfirmed via
a fresh grep this phase (§0's audit table). Not tenant-refactored, per
the explicit instruction not to touch dead code. Flagged as
housekeeping debt a future cleanup pass should remove, same as it was
flagged in the Phase 4 audit.

## 7. No cross-tenant fallback (§17 — verified by test, not just by reading code)

`test_no_fallback_to_first_organization_on_resolution_failure` proves
directly: with a real organization present in the database, a broken
resolver produces **zero** audit-event rows — not a row attached to
"the first organization it could find." Every `_resolve_context()` call
in `tenant_operational_sync.py` returns `None` (treated as "skip
entirely") rather than ever substituting a different organization than
the one it was actually trying to resolve.

## 8. Financial/patient data in operational shadow copies

`SecurityAuditEvent.detail` mirrors legacy `audit_event()`'s own
`detail` argument exactly — this module does not invent additional
redaction beyond what legacy already does, since the existing caller
survey (Phase 3/4 audits, reconfirmed here) found no call site passing
passwords, tokens, or full patient payloads into it.
`ExecutionQueueItem.payload`/`.result` mirror the exact JSON-text
representation `core/memory.py` already uses for the same data — not a
new exposure, the same shape moved into a second, equally
access-controlled store.

## 9. Live Phase 1 auth/RBAC — deliberately still dormant

`build_user_context()` exists and is fully tested, but **is not called
by any live path**. `core/auth.py` is unchanged (zero diff). The live
Streamlit login remains the shared-password system. No organization
switcher exists. This is intentional — Phase 5's job is to make the
*backend* ready to accept a real `AuthenticatedIdentity`-backed
`TenantContext` later, not to wire it into the live session today. When
the explicit auth-cutover phase happens, it has a tested constructor to
call rather than needing to invent one under time pressure.

## 10. RBAC enforcement — deliberately still dormant

Nowhere in Phase 5 does an operation check `TenantContext.permissions`
before proceeding — `has_permission()` exists on the dataclass (mirrors
`AuthenticatedIdentity.has_permission()` from Phase 1) but nothing
calls it yet. Tenant *isolation* (this phase) and permission
*enforcement* (a distinct, deferred phase) are kept separate on
purpose, per the task's own explicit instruction.

## 11. Integration credentials — unchanged, documented limitation

Adapters (`integrations/calendar_service.py`, `gmail_service.py`,
`whatsapp_service.py`) still use current environment-global
credentials. `ExecutionQueueItem` (both legacy and its Phase 5 shadow
copy) already carries `organization_id`, so a future Phase 6 that adds
per-clinic credential resolution has the organization context it needs
attached to every queued action already — it does not need to modify
Phase 5's shadow-sync shape to get it.

## 12. Marketing-site lead endpoint — unchanged, documented limitation

`marketing-site/api/lead.py` remains outside the tenant-context
boundary — a separate deployment, raw-SQL write path, predating any
organization concept (documented since Phase 3). Not rebuilt in Phase
5. Leads it creates still land in `memory_store` and are picked up like
any other legacy row by `scripts/backfill_v2_crm.py`/`repair_v2_crm.py`
— those tools' assumptions remain valid, since they were never aware of
*how* a legacy row was created, only that it exists in `memory_store`.
A future phase will need an explicit tenant/API strategy for this
endpoint if multiple organizations ever share one deployment for real.

## 13. Rollback

```
LEADLENS_V2_TENANT_CONTEXT_ENABLED=false
```

Restores exactly pre-Phase-5 behavior for approvals, the execution
queue, audit events, and the scheduler ledger — all four continue to
operate purely on `memory_store` as before, with zero relational shadow
writes attempted. No database action is needed. Phase 3's
`LEADLENS_V2_DUAL_WRITE_ENABLED` and Phase 4's `LEADLENS_V2_READ_*`
flags are completely independent of this switch and of each other —
confirmed unchanged by `git diff` on `relational_sync_service.py` and
`crm_read_router.py`'s flag logic.

## 14. Production validation procedure

1. Deploy Phase 5 code with `LEADLENS_V2_TENANT_CONTEXT_ENABLED` unset
   (off) — zero behavior change from Phase 4's deployed state.
2. Enable `LEADLENS_V2_TENANT_CONTEXT_ENABLED=true`.
3. Perform a few real approval create/approve/execute cycles and a few
   real CRM mutations (which already trigger `audit_event()`).
4. Confirm rows appear in `approvals`, `execution_queue_items`,
   `security_audit_events`, and `scheduler_alert_ledger` (after the
   next scheduler run) with the expected `organization_id`.
5. Roll back at any point with the flag alone if anything looks wrong.

## Do not implement (Phase 5 non-goals, confirmed untouched)

Live login cutover, organization switcher, live RBAC UI enforcement,
per-clinic integration credentials, subscription billing, public
multi-tenant API, webhook redesign, model-native LLM tool calling,
Jarvis feature/prompt/routing redesign, UI redesign, removal of legacy
CRM, disabling dual-write, removal of Phase 4 read-rollback flags.

## Do not rebuild

Same list as the prior phases' docs, unchanged by Phase 5: specialist
orchestration, the approval/execution engine's actual decision logic,
automation qualification logic, integration adapters, the current
Streamlit UI, the Docker foundation, Phase 3's dual-write, Phase 4's
read routing.
