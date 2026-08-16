# V2 Phase 6 — Per-Clinic Integration Credentials

Status: implemented, tested, **not yet activated in any live deployment**
(`LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` and per-organization rows
are both opt-in — see "Rollback" below). Builds on Phase 5's
`TenantContext` (`core/identity/tenant_context.py`) and reuses Phase
0-5's relational foundation; does not touch CRM, Jarvis, auth, or RBAC.

## 0. Audit of current integration paths (performed before implementation)

| Env var | Provider | Classification | Notes |
|---|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | WhatsApp | **A** — org-specific secret | Meta Cloud API bearer token |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp | **B** — org-specific config | Identifies the business's WhatsApp number |
| `WHATSAPP_API_VERSION` | WhatsApp | **D** — deployment/runtime config | Graph API version pin, has a safe code default (`v23.0`) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Gmail + Calendar | **A** — org-specific secret | Service-account private key (shared today across both adapters) |
| `GMAIL_DELEGATED_USER` | Gmail | **B** — org-specific config | Domain-wide-delegation impersonation target |
| `GOOGLE_CALENDAR_ID` | Calendar | **B** — org-specific config | Which calendar to write events into |
| `OPENAI_API_KEY` | Jarvis/AI | **C** — platform-level secret | See section 11 — deliberately NOT made tenant-specific |

Live call sites traced from `app.py`/`dashboard.py`/the scheduler entrypoint:

| Call site | Path | Classification |
|---|---|---|
| `services/integration_manager_v21.py` `integration_statuses()`, `execute_item()` | Approval-gated queue — reachable from `ui/*` Action Center pages | **B** — now tenant-scoped (this phase) |
| `services/appointment_messaging.py` `_send()` (via `send_appointment_confirmation`/`send_appointment_rsvp_reminder`) | Pre-authorized transactional WhatsApp — booking confirmation (`ui/patient_crm.py`) and 24h RSVP reminder (`scheduler/run_scheduled_checks.py`'s `appointment_reminder` check) | **B** — now tenant-scoped (this phase) |
| `integrations/calendar_service.py`, `gmail_service.py`, `whatsapp_service.py` | The three adapters themselves | **B** — credential acquisition refactored only, API-calling logic untouched |
| `marketing-site/api/lead.py` | Separate deployment | **E** — deferred, documented (section 12) |

No dead/unreachable integration code was found — all three adapters and both call sites are live.

## 1. Organization-scoped integration model

One provider-neutral table, `core/db/models/integration.py`'s
`OrganizationIntegration` — not one table per provider, since every
provider reduces to the same shape (some secret fields, some
non-secret configuration fields):

```
OrganizationIntegration
  id, organization_id (FK, CASCADE), provider (GMAIL | GOOGLE_CALENDAR | WHATSAPP)
  status (ACTIVE | DISABLED | ERROR | UNCONFIGURED)
  encrypted_credentials (Text, nullable — Fernet ciphertext of a JSON blob)
  encryption_format_version, encryption_key_version
  configuration (Text, nullable — plain JSON, non-secret fields)
  created_at, updated_at, last_verified_at, last_error_at, last_error_code
  UNIQUE(organization_id, provider)
  INDEX(organization_id, provider)
```

Migration `026598ba5867_phase6_organization_integration_credentials.py`
(also adds the Phase 5 `SecurityAuditEvent.organization_id` index —
section 9 below). Upgrade/downgrade/re-upgrade and `alembic check`
(zero drift) all verified.

## 2. Secret storage

Fernet (`cryptography` library — AES-128-CBC + HMAC-SHA256 authenticated
encryption; not a custom scheme) via `services/credential_encryption.py`.
Master key: `LEADLENS_CREDENTIAL_ENCRYPTION_KEY`, environment-only,
**never written to the database**. Secret fields for one provider are
JSON-encoded as one blob, then encrypted as one Fernet token; non-secret
configuration fields are stored as plain JSON in a separate column
(safe to read for status displays without decrypting anything).

## 3. Key rotation readiness

Each row stores `encryption_key_version` (which key encrypted it) and
`encryption_format_version` (currently `1`). A future rotation would add
`LEADLENS_CREDENTIAL_ENCRYPTION_KEY_V<N>` env vars for old key versions
(so old rows keep decrypting) and bump the active version via
`LEADLENS_CREDENTIAL_ENCRYPTION_KEY_VERSION`; re-encrypting existing
rows under a new key is a deliberate future script, not built here —
building a full key-management system now would be over-engineering
for today's single-clinic-scale deployment. Documented, not implemented
beyond the version fields themselves (per spec section 3's explicit
"do not overbuild").

## 4-5. Provider-specific configuration / credential service

`services/integration_credentials.py`'s `SECRET_FIELDS`/`CONFIG_FIELDS`
tables (mirroring section 0's audit exactly):

- **WhatsApp**: secret `access_token`; config `phone_number_id`, `api_version`.
- **Gmail**: secret `service_account_json` OR `service_account_file` (either accepted — see below); config `delegated_user`.
- **Google Calendar**: secret `service_account_json` OR `service_account_file`; config `calendar_id`.

`configure_integration()` / `disable_integration()` / `resolve_credentials()`
/ `resolve_provider_credentials()` / `safe_metadata()` / `validate_fields()`
are the module's full public surface — no function anywhere in it
accepts a caller-supplied `organization_id` or `integration_id`
(verified by `tests/test_phase6_integration_credentials.py::test_no_function_can_fetch_a_foreign_integration_by_raw_id`,
mirroring the same signature-inspection test pattern Phase 5 used).

**Google service-account credential**: `integrations/gmail_service.py`
and `calendar_service.py` were extended (not rewritten) to accept either
the raw JSON key content (`service_account_json`, via google-auth's
`Credentials.from_service_account_info()` — the real per-organization
case, since each clinic has its own Google Workspace / service account)
or a file path (`service_account_file`, matching the legacy single-file
env behavior exactly, for the transitional organization). No new OAuth
flow was invented.

## 6. No caller-injectable organization

Every adapter factory in `services/integration_clients.py`
(`get_whatsapp_client`, `get_gmail_client`, `get_calendar_client`) takes
a `TenantContext`, never an `organization_id`. The two live call sites
(`services/integration_manager_v21.py`, `services/appointment_messaging.py`)
resolve a `TenantContext` via the same trusted
`build_transitional_context()` every other Phase 5 hook point uses —
never from user-facing input.

## 7-8. Environment fallback — the adversarial-safety rule

`LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` (defaults OFF). When on,
`resolve_credentials()` allows the legacy env-var credentials **only**
when both are true:

1. no ACTIVE tenant row exists for (organization, provider) — a
   DISABLED or ERROR row is a hard stop, never falls back;
2. `tenant_context.organization_id` is **exactly** the transitional/
   default organization (`resolve_transitional_organization_id()` —
   the same non-injectable resolver Phase 2-5 already use).

Any other organization with an absent credential gets `None` — never
the deployment's env credentials, even with the flag on. This is
verified by four dedicated tests (`test_env_fallback_allowed_for_transitional_org`,
`test_env_fallback_denied_for_unrelated_org`,
`test_env_fallback_disabled_by_flag`,
`test_tenant_credential_overrides_env_fallback`).

## 9. Security-audit index debt (Phase 5 finding, closed here)

`SecurityAuditEvent.organization_id` now has an explicit index
(`ix_security_audit_events_organization_id`), added in the same Phase 6
migration. No query feature was built against it — this is purely the
small, safe schema improvement Phase 5's audit flagged.

## 10. Adapter changes

`integrations/whatsapp_service.py`, `gmail_service.py`,
`calendar_service.py` each gained one optional keyword-only constructor
parameter, `credentials: dict | None = None`. Omitting it (every
existing caller before this phase) reproduces the exact prior behavior
byte-for-byte — reads the deployment env vars, dry-runs if unset. Their
`send_text()` / `create_draft()` / `send_email()` / `create_event()`
request-building and error-handling logic is completely untouched.

## 11. OpenAI / LLM configuration

**Not made tenant-specific.** `OPENAI_API_KEY` remains platform-scoped
— there is no existing bring-your-own-LLM-key requirement in this
product. Clinic integrations (WhatsApp/Gmail/Calendar) are genuinely
per-organization business accounts; the LLM is shared platform
infrastructure Jarvis runs on, not something a clinic brings its own
credential for. This distinction is deliberate per spec section 31 and
is not revisited by this phase.

## 12. Marketing-site endpoint

`marketing-site/api/lead.py` is unchanged and remains outside this
credential-resolution boundary — it is a separate deployment with its
own raw-SQL write path, unaffected by (and not expected to consume)
per-organization integration credentials. Deferred, as in Phase 3/4/5.

## 13. Auth / RBAC

Unchanged. `core/auth.py` remains the live login gate. No organization
switcher. `configure_integration()`/`disable_integration()` accept a
`TenantContext` (Phase 1-ready) so a future auth cutover can protect
them with real `authorize()` checks without redesigning this module —
they are not wired into any live admin UI yet (backend-first, per spec
section 20/34).

## 14. Testing

`tests/test_phase6_integration_credentials.py` — 32 tests: encryption
(roundtrip, missing/wrong key, corrupted ciphertext, redaction), model
(uniqueness, A/B independence, disabled status), resolution (A gets A/B
gets B, no raw-ID fetch path, missing/disabled → None), env fallback (4
adversarial cases above), all three adapter factories (A/B isolation),
scheduler + approval-queue propagation, audit (no secrets), the
migration script (dry-run, idempotent, never overwrites, never prints
secrets), and failure-closed behavior (DB unavailable, key unavailable,
corrupted ciphertext, wrong provider).

One real bug was found and fixed during this phase's own testing (not
a pre-existing defect): `_current_tenant_context()` in
`integration_manager_v21.py`/`appointment_messaging.py` originally
constructed an independent `make_engine()` call rather than reusing
`services/integration_credentials.py`'s cached engine — harmless in
production (both read the same `DATABASE_URL`) but meant org resolution
and credential lookup weren't guaranteed to hit the same database
connection/cache. Fixed by having both call sites resolve their
`TenantContext` through `integration_credentials._get_engine()`
directly.

## 15. Rollback

Two independent kill switches, both defaulting to the pre-Phase-6
state:

- No `OrganizationIntegration` rows exist until an operator runs
  `scripts/migrate_integration_credentials.py` or calls
  `configure_integration()` — until then, `resolve_credentials()`
  always returns "absent", and every adapter behaves exactly as before
  this phase (env vars or dry-run).
- `LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED=false` (default) means
  even an absent tenant row never triggers the transitional fallback —
  though this only matters once real per-organization rows exist for
  *other* organizations; the single-clinic deployment today has no
  tenant rows at all, so its adapters already use the untouched
  env-var path regardless of this flag.

No DB deletion is ever required for rollback — disable the flag and/or
stop calling the migration script.

## 16. Production migration procedure (conservative, provider-by-provider)

1. Deploy Phase 6 with `LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` unset
   (default off) — verify existing integrations behave identically
   (adapters still read env vars directly, since no tenant rows exist).
2. Set `LEADLENS_CREDENTIAL_ENCRYPTION_KEY` (generate via
   `Fernet.generate_key()`) in the deployment's secret store.
3. Run `python scripts/migrate_integration_credentials.py --provider whatsapp --dry-run`
   against the transitional organization; review the reported fields
   (never a secret value).
4. Run it for real (no `--dry-run`).
5. Run `python scripts/verify_integration_credentials.py` — confirm
   `configured=True`, `credential_decryptable=True`,
   `required_fields_present=True`.
6. Send one controlled test WhatsApp message via the existing Action
   Center flow; confirm it uses the migrated credential (adapter
   `status()` now reports `configured: true` from the tenant row).
7. Repeat steps 3-6 for `gmail`, then `calendar`.
8. Repeat the whole sequence for any additional organization when this
   deployment genuinely becomes multi-clinic (not the case today).
9. Leave `LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED` set during a
   stabilization period so a credential-resolution bug degrades to the
   old env-var behavior rather than breaking sends.
10. Remove the legacy env vars only in a later, separate phase once
    every clinic has a verified tenant credential — **not done here**.

## Do not implement (confirmed untouched)

Live auth cutover, organization switcher, full RBAC UI, billing,
subscription management, multi-account-per-provider support, an OAuth
consent UI, public APIs, webhook redesign, Jarvis/AI redesign,
model-native tool calling, CRM migration changes, disabling Phase 3
dual-write, removal of Phase 4 read flags.

## Do not rebuild

The existing adapters' actual API-calling logic (`send_text`,
`create_draft`/`send_email`, `create_event`), the approval/execution
engine's decision logic, the scheduler's 14 check functions, the
current Streamlit UI, `marketing-site/api/lead.py`.
