# V2 Phase 7 — Live Authentication Cutover + RBAC Enforcement

Status: implemented, tested, **not yet activated in any live deployment**
(`LEADLENS_V2_AUTH_ENABLED` defaults OFF — the legacy shared-password
gate remains the production login path until an operator explicitly
flips it). Builds on Phase 1's identity/authorization backend
(`core/identity/`) and Phase 5's `TenantContext`; does not touch CRM
storage, Jarvis's prompting/routing, or the Streamlit UI's visual
design.

## 0. Audit of the live auth flow (performed before implementation)

`app.py` calls `core.auth.require_login()` once, at the very top, before
any business data loads; `dashboard.py` reads `core.auth.current_role()`
(legacy) to decide `ROLE_CRM_ONLY` routing and calls
`core.auth.render_logout_control()` from the sidebar. The legacy gate
(`APP_PASSWORD`/`APP_PASSWORD_RECEPTIONIST`, shared per-deployment
secrets, not per-user accounts) had one genuinely reusable piece: a
short-lived HMAC `reload_token()` that survives the forced full-page
browser navigation `ui/workspace_theme.py` triggers on Core-switch —
`st.session_state` doesn't survive a real navigation, only the URL
does, so login would otherwise repeat on every workspace switch. This
phase preserves that mechanism's *purpose* exactly, under its own V2
signing key.

## 1. Design

```
email + password
  -> core.identity.authentication_service.authenticate()
  -> User (Argon2id) + active Membership(s)
  -> (auto-resolve if exactly one; minimal org picker if more than one)
  -> core.identity.session.AuthenticatedSession (st.session_state)
  -> core.auth.current_authenticated_session() revalidates on every call
     via core.identity.tenant_context.build_user_context()
     (core.identity.authorization_service.resolve_identity() underneath)
  -> TenantContext for live app actions
```

`core/auth.py` now has two complete, independent login paths, selected
by `v2_auth_enabled()` (`LEADLENS_V2_AUTH_ENABLED`):

- **Legacy** (default): unchanged from before this phase, byte-for-byte
  — `_require_login_legacy()` / `_render_logout_control_legacy()` are
  the original functions, renamed but not modified.
- **V2**: `_require_login_v2()` / `_render_logout_control_v2()`.

**There is never a session where both are simultaneously valid** — the
dispatch is a hard `if/else` on the flag, not a fallback chain.

## 2. HMAC reload token — preserved, not replaced

`_v2_reload_token()` signs `{timestamp}:{user_id}:{organization_id}`
with an HMAC key dedicated to V2 sessions
(`LEADLENS_V2_AUTH_SESSION_SECRET`, or a process-local random key if
unset — see the function's docstring for why that's safe: an
invalidated 20-second token after a process restart just forces one
harmless extra login). On restore
(`_restore_session_from_reload_token()`), the token's claimed
`(user_id, organization_id)` pair is fed straight into
`build_user_context()` — the exact same full database check
`current_authenticated_session()` itself uses. **A token can never
carry a trusted role, organization switch, or elevated permission of
its own** — it only ever triggers one fresh, real authorization check.
Tested adversarially: an expired token, and a token with a
hand-tampered (not re-signed) `organization_id`, are both rejected
(`tests/test_phase7_auth_cutover.py`).

## 3. Membership selection

`authenticate()` returns every ACTIVE membership for the verified
user. Exactly one → auto-resolved, no extra screen. More than one → a
minimal `st.radio` picker (`_render_organization_picker()`) built
**only** from that exact list — never an arbitrary organization id, and
never re-checking the password. Zero → generic "no active organization
access" error (fails closed, not a hint that the email exists).

## 4. Session architecture

`core/identity/session.py`'s `AuthenticatedSession` stores only
`user_id`, `email`, `organization_id`, `organization_name`,
`membership_id`, `role`, and the resolved permission set — never a
password, hash, or decrypted integration secret.
`current_authenticated_session()` is the **only** trusted accessor:
every call re-derives role/permissions fresh from the database via
`build_user_context()` and overwrites the stored session if anything
changed, or clears it entirely if the user/membership/organization is
no longer valid. Practical validation cadence for Streamlit's rerun
model: **every** call to `current_authenticated_session()` (which
`require_login()` and `services/authorization_guard.py` both call)
does a fresh, cheap (indexed) DB check — no TTL cache, since clinic-
scale traffic doesn't need one and staleness would directly contradict
section 7's "no indefinite stale elevated access" requirement.

## 5. Logout / session isolation

`core.identity.session.clear_session()` removes the authenticated
identity key and every `crm_*`/`jarvis_*`/`workspace_*`-prefixed UI
state key, so a second login in the same browser tab never shows a
flash of the previous user's selected patient/workspace page before
their own data loads. Tested: user A logs in, logs out, user B logs in
— B's session carries none of A's identity or organization
(`test_logout_clears_session_and_prevents_next_user_bleed`).

## 6. RBAC — backend-first, per Phase 1's existing matrix

`services/authorization_guard.py`'s `require_permission(permission)` is
the **one** chokepoint every gated call goes through. It is a no-op
(always allows) when V2 auth is off, or when no live authenticated
Streamlit session exists for this call — covering scripts, the test
suite, and Phase 5's system/scheduler actors, none of which this
phase's RBAC is meant to restrict (they remain tenant-scoped by Phase
5's separate, already-enforced `TenantContext` design).

**Wired chokepoints** (backend-authoritative; UI hiding is a courtesy
on top, never a substitute):

| Boundary | Permission | File |
|---|---|---|
| CRM create/update/archive (entity-mapped — see table below) | `<entity>.manage` | `services/clinic_data_service.py` |
| Approve/reject a queued action | `automations.approve` | `services/integration_manager_v21.py::decide_item` |
| Execute an approved action | `automations.approve` | `services/integration_manager_v21.py::execute_item` |
| View the execution queue | `automations.view` | `services/integration_manager_v21.py::execution_rows` |
| Configure/disable an integration credential | `integrations.manage` | `services/integration_credentials.py` |
| View/create/change-role/disable organization members | `members.view` / `members.manage` | `services/member_management.py` |

CRM entity → permission mapping (Phase 1's matrix has no per-CRM-entity
permission of its own — this is a documented product-judgment mapping,
not a Phase 1 given):

| Entity | Permission | Reasoning |
|---|---|---|
| patients | `patients.manage` | direct match |
| appointments | `appointments.manage` | direct match |
| packages, package_templates, progress_notes | `treatments.manage` | treatment-plan data |
| payments | `payments.manage` | direct match |
| therapists | `appointments.manage` | staff roster used for scheduling; no dedicated staff permission exists |
| leads, corporate_clients | `leads.manage` | business development, grouped together |

## 7. Jarvis / specialist RBAC

`services/jarvis_tools.py::run_read_only_tool(name, context, *, permissions=None)`
gates at the **data boundary**, not the prompt: `revenue_summary` (a
finance-only tool) returns an explicit access-denied stub without
`jarvis.finance`; `business_snapshot` (a mixed tool) has its
`financial_snapshot` field stripped. **A real, previously-undetected
information-boundary gap was found and fixed here**:
`business_snapshot` carried the exact same financial data
`revenue_summary` does, under a different tool name reachable by
non-finance specialists (marketing, HR, operations) — now closed by the
same field-level redaction rule.

`services/specialist_orchestration.py::coordinate_specialists(..., permissions=None)`
additionally gates specialist *selection*: the `finance` specialist
requires `jarvis.finance`, `marketing` requires `jarvis.marketing`; a
caller entirely lacking `jarvis.use` is refused before any specialist
runs at all. `permissions=None` (every pre-Phase-7 caller) reproduces
prior behavior exactly — live wiring only activates through the two
real UI call sites (`ui/jarvis_mode.py`, `services/business_jarvis_engine.py`),
both updated to pass `services.authorization_guard.current_permissions()`.

## 8. Member management

`services/member_management.py` — every function takes a `TenantContext`
and operates only on `tenant_context.organization_id`; there is no
function anywhere in the module that accepts a caller-supplied
organization id, so an admin can never manage membership in another
organization. Last-active-OWNER protection: `LastOwnerError` on any
attempt to disable or demote an organization's sole remaining active
OWNER. Minimal UI: `ui/member_management.py`, reachable only when V2
auth is on and the caller has `members.view` (`dashboard.py`'s sidebar
only lists "Organization Members" in that case) — the backend check is
still authoritative regardless.

## 9. Rate limiting

Session-local (not distributed): 5 failed attempts locks the login form
for 30 seconds (`core.auth._MAX_FAILED_ATTEMPTS` /
`_LOCKOUT_SECONDS`), reset on any success. **Documented limitation**:
this is per-browser-session state, not IP- or account-wide — a
determined attacker opening fresh sessions bypasses it. Real production
hardening (IP-based or account-wide throttling) belongs at the hosting
platform/reverse-proxy layer, out of scope for this phase per its
"do not build a huge distributed rate-limit architecture" instruction.
Every failed and successful login is recorded via the existing
`services/security_service.audit_event()` (which already shadow-syncs
into the tenant-scoped `SecurityAuditEvent` table per Phase 5) — never
with a password or hash in the detail field, and the on-screen message
is always the generic "Invalid email or password." regardless of
whether the email exists.

## 10. What did NOT change

- `core/db/`, `core/identity/`'s existing services (Phase 1) — used,
  not modified in behavior.
- Phase 3 CRM dual-write, Phase 4 read-routing flags, Phase 5
  `TenantContext`/shadow-sync design, Phase 6 credential resolution
  and its transitional env fallback — all zero-diff.
- The CRM/Jarvis UI's visual design, navigation structure beyond the
  one new "Organization Members" page and the `is_crm_only_role`
  check becoming V2-aware.
- Scheduler/system actors — Phase 5's `build_transitional_context()`/
  `build_system_context()` continue to run without any human login,
  exactly as before; RBAC is additive on top of human Streamlit
  sessions only.

## 11. Known scope boundaries (explicitly not built, per spec)

No `streamlit.testing.v1.AppTest`-based full UI form-interaction tests
were written — every test in `tests/test_phase7_auth_cutover.py` calls
the underlying backend/session functions directly (confirmed
`st.session_state` behaves as a real dict in "bare mode" outside a
`streamlit run`, so this is genuine functional testing of the
authoritative logic, not a mock). Full widget-click-level UI testing
is a reasonable follow-up, not a gap in the actual security boundary,
since the backend checks are what's authoritative by design.

No password-reset/email flow, no SSO, no organization switcher beyond
the minimal picker, no invitation emails, no billing — all explicitly
out of scope per the spec's "do not implement" list.

## 12. Rollback

Set `LEADLENS_V2_AUTH_ENABLED=false` (or unset it). The legacy gate
resumes immediately and exactly as before. No data is deleted by
either direction — Phase 1's identity tables and the legacy env-var
credentials both persist regardless of which path is active. Legacy
credentials should be retained (not removed) for a period after
cutover specifically to support this rollback path — removal is a
later, separate hardening phase.

## 13. Production runbook

1. Deploy this phase's code with `LEADLENS_V2_AUTH_ENABLED` unset —
   verify the existing legacy login still works exactly as before
   (nothing in this phase changes behavior until the flag is set).
2. Bootstrap the owner identity:
   `python scripts/bootstrap_identity.py --org-slug <slug> --org-name "<Clinic Name>" --owner-email <email>`
   (prompts for a password via `getpass` if `--owner-password` is
   omitted — never printed to the terminal). Idempotent; safe to rerun.
3. Verify the record: `python scripts/verify_v2_crm_read_parity.py`
   type sanity aside, directly query (or use a short Python snippet
   against `core.identity.organization_service`/`user_service`) to
   confirm the organization, user, and OWNER membership exist and are
   ACTIVE.
4. Test V2 authentication safely, still with the flag off: run
   `pytest tests/test_phase7_auth_cutover.py` against the same
   `DATABASE_URL` the deployment will use (isolated test fixtures, but
   confirms the environment/DB wiring is sound) — or set the flag on a
   staging deployment first if one exists.
5. Enable `LEADLENS_V2_AUTH_ENABLED=true` in the deployment's
   environment/secrets.
6. Log in as the bootstrapped owner with the real email/password.
7. Verify the organization context shown in the sidebar (organization
   name, role) matches expectations.
8. Test CRM: create/view a patient record.
9. Test Jarvis: ask a question, confirm a normal response.
10. Test approvals: prepare, approve, and execute one low-risk action
    (e.g. a Gmail draft in dry-run mode).
11. Test integrations: view integration status; if per-clinic
    credentials are already migrated (Phase 6), confirm they still
    resolve correctly.
12. Create and test at least one restricted-role account (e.g.
    RECEPTIONIST) via "Organization Members" — confirm it can log in.
13. Verify denial behavior: confirm the restricted account cannot
    reach Payments/Settings/Organization Members, and that a direct
    backend call (e.g. attempting `payments.manage`) is denied, not
    merely hidden.
14. Monitor `security_audit_log` / the relational `SecurityAuditEvent`
    table for `login_success`/`login_failed`/`logout` entries over the
    following days.
15. Rollback path if anything is wrong: set
    `LEADLENS_V2_AUTH_ENABLED=false` — the legacy gate resumes
    immediately, no data loss.

Legacy shared credentials (`APP_PASSWORD`/`APP_PASSWORD_RECEPTIONIST`)
should remain configured for a stabilization period after cutover
(supporting step 15's rollback) and can be removed in a later,
separate hardening phase once V2 auth has run in production without
incident.

## Do not implement (confirmed untouched)

Subscription billing, full customer self-signup, elaborate invitation
email systems, password-reset email flow, SSO, social login,
organization billing, broad UI redesign, React, CRM storage cleanup,
migration-flag cleanup, public API redesign, Jarvis redesign.

## Do not rebuild

The existing CRM/Jarvis Streamlit UI's layout and visual design, the
approval/execution engine's actual decision logic, the scheduler's 14
check functions, the integration adapters' API-calling logic, Phase 1's
identity backend itself (`core/identity/`) — all reused, none rewritten.

## Phase 7.1 addendum — authentication + RBAC hardening

A separate, independent audit pass (AUDIT ONLY — see the "Audit LeadLens
CareOS V2 Phase 7" report) found three real defects in the Phase 7 build
above, none caught by Phase 7's own 311-test suite. Phase 7.1 fixed all
three with minimal, focused changes:

1. **Reload-token reuse after logout.** `clear_session()`
   (`core/identity/session.py`) only ever cleared `st.session_state` —
   it never invalidated a reload token already handed to the browser
   (embedded in the URL by `ui/workspace_theme.py`'s forced-navigation
   theme mechanism on every CRM↔JARVIS switch). Since `st.rerun()` (what
   the logout button triggers) is not a real browser navigation,
   `st.query_params` survives it, so a token minted moments earlier
   could silently restore the just-logged-out session on the very next
   rerun, within its 20-second TTL. Fixed with defense in depth:
   - **A.** `_render_logout_control_v2()` (`core/auth.py`) now
     explicitly deletes `st.query_params["_auth"]` as part of logout.
   - **B.** `clear_session()` now stamps a
     `_session_logout_epoch` marker (deliberately excluded from its own
     stale-key wipe) with the current time. `_v2_reload_token_identity()`
     (`core/auth.py`) rejects any token whose embedded timestamp is at or
     before that epoch, even if the token is still within its own TTL —
     see `core/identity/session.py::logout_epoch()`.
   - Neither change weakens the token's HMAC signature check or TTL, and
     neither replaces the scheme with JWT.
2. **`services/platform_data.py::save_company_profile()` had no RBAC
   gate.** Any authenticated role could mutate organization-level clinic
   settings (the CRM "Settings > Clinic details" tab,
   `ui/data_hub.py`). Now requires `organization.manage` via
   `services/authorization_guard.require_permission()` — OWNER-only per
   the unchanged Phase 1 matrix, backend-authoritative.
3. **The audit/security log had no RBAC gate.** Any authenticated role
   could view the full audit log (the CRM "Settings > Data protection"
   tab, `ui/phases_16_to_20.py::show_security_center()`). The read
   function itself — `services/security_service.py::audit_rows()` — now
   requires `audit.view` (OWNER/ADMIN/FINANCE per the Phase 1 matrix);
   this is its only live caller, so gating the read closes the boundary
   at its source. `audit_event()` (the write function, called from the
   scheduler and every live writer) was deliberately left ungated.
   `show_security_center()` wraps the call in try/except so a denied
   viewer sees a clean message instead of a crash.

No schema change, no new Alembic migration, no new environment
variable — this was a bug-fix pass on top of Phase 7's existing
mechanisms, not a new phase of scope. See
`tests/test_phase7_1_hardening.py` (24 tests) and the updated
`tests/test_phase7_audit.py` (its two "no RBAC gate" findings are now
inverted into "gate is present and enforced" tests) for verification.

## Phase 7.1.1 addendum — onboarding RBAC + reload-token timestamp precision

An independent audit of Phase 7.1 (AUDIT ONLY pass) found two further,
narrower defects, both fixed here with no new files/migrations beyond
one new test file:

1. **Onboarding bypassed `organization.manage`.** `onboarding.py`
   called `core.memory.save_company()` directly — a separate, always-
   ungated function from the correctly-gated
   `services.platform_data.save_company_profile()`. Since `app.py` runs
   `require_login()` before routing to onboarding whenever
   `core.memory.company_exists()` is False, *any* authenticated V2-auth
   identity (not just one with `organization.manage`) could initialize
   the company profile during that first-run window. Fixed by routing
   `onboarding.py::_step_tour()` through `save_company_profile()`
   instead — the same gate `ui/data_hub.py`'s "Settings > Clinic
   details" tab already used. `core.memory.save_company()` itself
   remains intentionally ungated (documented as a low-level
   bootstrap/script primitive), but the live human UI path no longer
   calls it. In legacy mode (`LEADLENS_V2_AUTH_ENABLED=false`),
   `require_permission()` is a no-op, so onboarding behaves exactly as
   before.
2. **Reload-token timestamp precision mismatch.** `_v2_reload_token()`
   minted tokens with `int(time.time())` while
   `core.identity.session.logout_epoch()` stores a full-precision
   float; comparing a truncated integer against a float epoch could
   wrongly reject a token minted in the very same wall-clock second as
   a *prior* logout, even when the mint genuinely happened after a
   fresh relogin. Fixed by using full-precision (`repr(time.time())`)
   timestamps in `_v2_reload_token()`, and parsing with `float()` in
   `_v2_reload_token_identity()` — which also parses old integer-string
   tokens identically, so no format flag or migration was needed for a
   ~20-second-TTL token. Expiry and HMAC/tamper validation are
   unchanged.

Also included as a trivial, in-scope UX cleanup: `ui/data_hub.py`'s
"Save business memory" button and `onboarding.py`'s final step now both
catch `PermissionDenied` and show a clean denial message instead of an
uncaught exception — matching the pattern `show_security_center()`
already used. Backend enforcement itself is unchanged by this cleanup.

See `tests/test_phase7_1_1_hardening.py` (20 tests) for verification,
including the exact same-second logout→relogin scenario the audit
reproduced.
