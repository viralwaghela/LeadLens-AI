# V2 Phase 1 — identity and authorization

Read this before touching `core/identity/`, `core/db/models/identity.py`,
`core/db/models/identity_audit.py`, or `scripts/bootstrap_identity.py`.
It exists so a future session doesn't accidentally wire this layer into
the live login path before that's actually the plan. Read
`docs/V2_COEXISTENCE.md` first — this document assumes that one's rules
still apply and only adds Phase 1-specific detail.

## The rule (unchanged from Phase 0, restated for this layer)

```
LeadLens (live, production)
  → core/auth.py
    → shared deployment credentials, Streamlit session role
      → this is STILL the only thing that gates access to the app

V2 identity layer (Phase 1, dormant)
  → core/identity/ (password/user/organization/membership/authorization/
    authentication/audit services)
    → core/db/models/identity.py, identity_audit.py (Phase 0 schema,
      extended)
      → nothing in app.py, dashboard.py, core/auth.py, services/, ui/,
        or scheduler/ imports anything from core/identity/
```

**`core/auth.py` remains the production login gate until an explicit
later phase changes that.** Phase 1 does not modify it, does not read
from it, does not write to it, does not call it, and is not called by
it. Verify this yourself with `git diff` against `core/auth.py`,
`app.py`, and `dashboard.py` after any Phase 1 work; if any of those
files show a diff, something went outside Phase 1's scope.

## What exists and what it's for

| Path | What it is | Live-wired? |
|---|---|---|
| `core/identity/password_service.py` | `hash_password()`/`verify_password()`, Argon2id via argon2-cffi | No |
| `core/identity/user_service.py` | User CRUD (create/get/disable/activate/record_login) | No |
| `core/identity/organization_service.py` | Organization CRUD (create/get/update/activate/deactivate/by-slug) | No |
| `core/identity/membership_service.py` | Membership CRUD, duplicate prevention, role changes | No |
| `core/identity/permissions.py` | Permission taxonomy + role → permission matrix (below) | No |
| `core/identity/authorization_service.py` | `authorize()` / `resolve_identity()` — the 7-step check | No |
| `core/identity/context.py` | `AuthenticatedIdentity` tenant-context object | No |
| `core/identity/authentication_service.py` | `authenticate(email, password)` → user + active memberships | No |
| `core/identity/audit_service.py` | Writes `IdentityAuditEvent` rows | No |
| `core/db/models/identity_audit.py` | `IdentityAuditEvent` model (new table, Phase 1) | No |
| `scripts/bootstrap_identity.py` | Idempotent CLI: org + owner user + OWNER membership | No — CLI-only, never imported |

## Why the role model isn't a straight copy of `services/security_service.py`

That file's `ROLE_PERMISSIONS` dict (Owner/Therapist/Receptionist/Viewer,
permissions like `view_finance`/`manage_users`) is real but **currently
unenforced** — no call site in the live app actually gates on it (see
that file: it only exports `mask_sensitive`, `audit_event`, and
`audit_rows`). Phase 1 was asked to build the smallest useful *SaaS*
role model, informed by that taxonomy but not bound to it. The result
adds three roles the clinic-only taxonomy didn't have a place for
(Admin, Finance, Marketing) and renames Therapist → Practitioner
(provider-neutral, since LeadLens is meant to serve fitness centers and
other service businesses too, not just physiotherapy — see `CLAUDE.md`).

## Role → permission matrix

| Permission | OWNER | ADMIN | RECEPTIONIST | PRACTITIONER | FINANCE | MARKETING | VIEWER |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| organization.view | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| organization.manage | ✓ | | | | | | |
| members.view | ✓ | ✓ | | | | | |
| members.manage | ✓ | ✓ | | | | | |
| patients.view | ✓ | ✓ | ✓ | ✓ | | | ✓ |
| patients.manage | ✓ | ✓ | ✓ | ✓ | | | |
| appointments.view | ✓ | ✓ | ✓ | ✓ | | | ✓ |
| appointments.manage | ✓ | ✓ | ✓ | ✓ | | | |
| treatments.view | ✓ | ✓ | ✓ | ✓ | | | ✓ |
| treatments.manage | ✓ | ✓ | | ✓ | | | |
| payments.view | ✓ | ✓ | ✓ | | ✓ | | |
| payments.manage | ✓ | | | | ✓ | | |
| finance.view | ✓ | | | | ✓ | | |
| leads.view | ✓ | ✓ | ✓ | | | ✓ | ✓ |
| leads.manage | ✓ | ✓ | ✓ | | | ✓ | |
| automations.view | ✓ | ✓ | ✓ | | | ✓ | ✓ |
| automations.approve | ✓ | ✓ | | | | | |
| automations.manage | ✓ | ✓ | | | | | |
| integrations.view | ✓ | ✓ | | | | | |
| integrations.manage | ✓ | ✓ | | | | | |
| jarvis.use | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| jarvis.finance | ✓ | | | | ✓ | | |
| jarvis.operations | ✓ | ✓ | ✓ | ✓ | | | |
| jarvis.marketing | ✓ | ✓ | | | | ✓ | |
| audit.view | ✓ | ✓ | | | ✓ | | |

Design intent, in one line per role:

- **OWNER** — everything. The only role that can manage the organization
  itself or hand out/revoke memberships to other Owners.
- **ADMIN** — runs the clinic day-to-day (patients, appointments,
  treatments, leads, automations, integrations, members) but cannot
  touch money (`payments.manage`, `finance.view`, `jarvis.finance`) or
  the organization record itself.
- **RECEPTIONIST** — front desk: patients, appointments, leads, can
  *see* payments (e.g. confirm a package balance) but not manage them,
  no finance visibility.
- **PRACTITIONER** — clinical: patients, appointments, treatment notes.
  No leads, no payments, no finance, no management permissions.
- **FINANCE** — payments, finance dashboards, the finance-flavored
  Jarvis tier, audit visibility. No clinical/patient access.
- **MARKETING** — leads and the marketing-flavored Jarvis tier only.
- **VIEWER** — read-only across non-financial clinic data. No payments,
  no finance, no manage permissions of any kind.

`jarvis.use` is granted to every role deliberately — it's the baseline
"can talk to Jarvis at all" gate; `jarvis.finance`/`jarvis.operations`/
`jarvis.marketing` are the sensitive-tool-tier gates layered on top of
it, matching the audit's recommendation to think about Jarvis
authorization before Jarvis itself is tenant-wired (spec section 10 —
`jarvis_tools.py`, `jarvis_context.py`, and
`services/specialist_orchestration.py` are all untouched by Phase 1;
see `tests/test_phase1_identity.py`'s "Future Jarvis authorization
tests" section for what's actually proven today: the *permission
system* can already answer these questions correctly, even though
nothing calls it yet).

## Authorization semantics

`core.identity.authorization_service.authorize(session, user_id=,
organization_id=, permission=)` performs, in order, and returns the
reason for the first failure:

1. `user_not_found`
2. `user_disabled`
3. `organization_not_found`
4. `organization_inactive`
5. `membership_not_found`
6. `membership_disabled`
7. `permission_denied`

Only if all seven pass does it return `allowed=True` along with an
`AuthenticatedIdentity` (user_id, organization_id, membership_id, role,
permissions). Supplying a syntactically valid `organization_id` is
never sufficient on its own — step 5/6 (a real, active membership row)
is unconditional. `tests/test_phase1_identity.py`'s adversarial section
proves this directly: an Owner of Organization A manually supplying
Organization B's real database ID still gets `membership_not_found`,
not access.

`resolve_identity()` is the same check minus step 7 — used when a
caller needs "is this user allowed into this organization at all,
and with what permissions" without a specific permission in mind yet
(this is what a future Streamlit session/context object would call).

## Password hashing

Argon2id via `argon2-cffi` (`core/identity/password_service.py`). No
custom cryptography — `PasswordHasher.hash()`/`.verify()` handle
salting, encoding, and parameters entirely; this module never touches
raw hash bytes. Chosen over bcrypt because a prebuilt Windows wheel
installs cleanly in this repo's dev environment with no build-tool
dependency, and Argon2id is the current OWASP-recommended default.

`User.password_hash` is never included in `__repr__` (see
`core/db/models/identity.py`), and `core/identity/audit_service.py`
strips any `password`/`password_hash`/`secret`/`token`/`api_key` key
from audit event `detail` payloads before writing, as defense in depth
even though no caller in this package currently passes one.

## Sessions — deliberately not built

Phase 1 stops at `authenticate(email, password) → user + active
memberships`. No JWT, no token object, no session store. The live app
is Streamlit, which has its own session mechanism
(`st.session_state`) — building a parallel session/token system before
there's an actual integration point to plug it into would be exactly
the kind of premature, unused infrastructure both this phase and
`CLAUDE.md`'s general engineering guidance warn against. A future,
explicit phase that actually wires this into Streamlit's login flow is
where that decision belongs.

## Enum changes to the Phase 0 schema, and why they're safe

`core/db/models/identity.py`'s `UserStatus`, `MembershipStatus`, and
`MembershipRole` enums were extended in Phase 1 (see that file's own
docstring for the full list). Because nothing live reads this schema
yet (same dormant-schema property as everything else in `core/db/`),
renaming/adding enum members here changes zero live behavior. The one
knock-on effect: `tests/test_phase0_schema.py` referenced
`MembershipRole.THERAPIST`, which was renamed to `PRACTITIONER` — that
test file was updated in the same commit to keep Phase 0's own suite
green after this dormant rename.

Migration `af9ede758982_phase1_identity_and_authorization.py` handles
the Postgres side of these enum changes explicitly (`ALTER TYPE ...
RENAME VALUE` / `ADD VALUE`, run in autocommit blocks) since Alembic's
autogenerate does not detect enum member changes on its own — see that
migration file's own docstring for the full detail, including the
known Postgres limitation that `ADD VALUE`s cannot be cleanly reversed
in `downgrade()` (there is no `DROP VALUE`).

## Bootstrap script

`scripts/bootstrap_identity.py` — explicit, manual, idempotent. Creates
(or confirms) an Organization + a User + an OWNER Membership linking
them, using `core.identity`'s own services (not duplicated logic).
Never run automatically; never overwrites an existing user's password
(`tests/test_phase1_identity.py::test_bootstrap_never_silently_overwrites_credentials`
proves this against a real subprocess run). This is preparation for a
future, separate, explicitly-discussed cutover phase — running it
against a real deployment's `DATABASE_URL` is harmless (it only adds
rows to tables nothing live reads), but be deliberate about which
database that is first, same as `scripts/bootstrap_beyond_pain_org.py`.

## Do not rebuild

Same list as `docs/V2_COEXISTENCE.md`'s, unchanged by Phase 1:
specialist orchestration, the approval/execution engine, automation
qualification logic, integration adapters, the current Streamlit UI,
the Docker foundation. Add to that list for Phase 1: **do not wire
`core/identity/` into `core/auth.py`, `app.py`, `dashboard.py`, or any
UI login form** without that being its own explicit, discussed phase —
see this document's own "The rule" section above.
