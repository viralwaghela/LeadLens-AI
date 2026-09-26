# LeadLens CareOS — Public Demo Environment (V2 Phase 10)

A public, portfolio-facing demo of LeadLens that anyone can open without a
login, that shows only **synthetic** data, and that cannot write, send,
export, or reach anything real — even if its own authorization logic failed.

- Live behaviour: visitors opening the URL are admitted straight away (no landing page, no click),
  and are admitted (server-side, passwordless) as the read-only demo viewer of
  a fictional clinic, *LeadLens Demo Clinic*. A banner reads **Demo Workspace —
  Sample Data Only**.
- It is a **separate deployment** (its own Streamlit app, its own PostgreSQL
  database, its own OpenAI project, its own secrets) built from the same
  repository. Nothing about a normal (production/client) deployment changes:
  every demo behaviour is inert unless `LEADLENS_DEMO_MODE` is set.

## 1. Architecture

```
                         PUBLIC INTERNET
                               |
   +---------------------------v----------------------------+      +---------------------------+
   | Streamlit app #2 (its own *.streamlit.app subdomain)   |      | Production (unchanged)    |
   | tracks a dedicated `demo` release branch               |      |  app #1 (client-1)        |
   | own secrets store: ONLY the demo values in section 4   |      |  production Postgres      |
   |                                                        |  no  |  WhatsApp / Gmail / Cal   |
   | LEADLENS_DEMO_MODE=1  -> code layers (section 2)       | path |  OpenAI (production key)  |
   +-----------+---------------------------+----------------+      |  GitHub Actions scheduler |
               |                           |                       +---------------------------+
   +-----------v----------+     +----------v-------------+
   | DEMO PostgreSQL      |     | OpenAI *demo Project*  |
   | its own project      |     | own key, provider-side |
   | synthetic data only  |     | budget cap + model     |
   +----------------------+     | allowlist              |
                                +------------------------+
```

**Infrastructure isolation is the real protection.** The demo app cannot reach
production data, keys, integrations or actions because none of them exist in its
environment. The code layers below are defense in depth for the case where
something is mis-configured.

## 2. Code layers (all server-side)

| Layer | What it does | Where |
|---|---|---|
| Startup tripwire | Refuses to boot on any production secret, non-Postgres / driver-qualified DB URL, missing flag, wrong default org, a database holding any non-demo organization, or an unmarked company profile. Shows visitors a generic page; details go to the log (names only, never values). | `core/demo_tripwire.py`, `ui/demo_entry.py` |
| Passwordless, demo-only entry | The server admits the visitor as `DEMO_VIEWER` of the org found by the demo slug **and** `is_demo`. No password/token exists; the demo user's hash is not a valid hash. Refuses outside demo mode. | `core/demo_session.py` |
| Permission ceiling | Any membership in an `is_demo` org — even a mistaken OWNER — is capped to the 12 read-only permissions. `DEMO_VIEWER` is refused in any non-demo org. Re-derived from the database on every access. | `core/identity/permissions.py`, `authorization_service.py` |
| Database write guard | One SQLAlchemy engine hook refuses every INSERT/UPDATE/DELETE/DDL (ORM, Core, raw `text()`), below the ORM. Only `demo_usage` (the LLM budget) is writable. | `core/db/demo_guard.py` |
| Legacy store guard | Refuses every write to the legacy `memory_store`, at the six low-level executors. | `core/memory.py` |
| External-action guard | Adapters are forced dry-run and hold no credentials; the credential factory never resolves any; queued actions never execute; the scheduler never runs; audit is not persisted. Not bypassable, even by the operator. | `integrations/*`, `services/integration_*`, `scheduler/*` |
| LLM limiter | Model pinned to `DEMO_OPENAI_MODEL`; prompt/output size caps; per-session, hourly and daily call limits (hourly/daily are global and survive restarts); fails **closed**. The request carries no tool definitions. | `core/demo_llm.py`, `services/ai.py` |
| Export / upload guard | Export bytes are never built; uploads never touch disk. Refused server-side, not just hidden. | `ui/demo_gate.py`, `services/platform_data.py` |

The demo permission set (`core/identity/permissions.py`): `organization.view`,
`patients.view`, `appointments.view`, `treatments.view`, `payments.view`,
`finance.view`, `leads.view`, `automations.view`, `jarvis.use`,
`jarvis.finance`, `jarvis.operations`, `jarvis.marketing`. **Nothing** that
manages, approves, or touches members, integrations, or the audit log.

## 3. Everything you must do yourself (checklist)

The repository work is finished; these steps need your accounts. Do them in
order. **Never paste a production value into any demo setting.**

### 3.1 Create the demo database
- [ ] Create a **new** PostgreSQL project/instance (a *separate project* at your
  provider — not a second database inside the production project).
- [ ] Copy its connection string as a **plain `postgresql://…` URL**
  (`?sslmode=require` is fine). Do **not** use a `+psycopg` suffix — the legacy
  store cannot parse it.

### 3.2 Create the demo OpenAI project
- [ ] In the OpenAI dashboard create a **new Project** (e.g. `leadlens-demo`).
- [ ] Set a **monthly budget cap** you are comfortable losing (this is the hard
  backstop behind the in-app limits).
- [ ] If available, restrict the project's **model allowlist** to the one model
  you set in `DEMO_OPENAI_MODEL`.
- [ ] Create an API key **inside that project**. This is `OPENAI_API_KEY` for
  the demo app only.

### 3.3 Migrate and seed the demo database (from your machine)
Use a shell that does **not** contain production variables. The seed/reset
commands refuse to run if any `WHATSAPP_*`, `GMAIL_*`, `GOOGLE_*`, `MASTER_*`,
`CLIENT*_`, `LEADLENS_CREDENTIAL_ENCRYPTION_KEY` or `APP_PASSWORD*` variable is set.

```bash
# 1. Migrate the demo database (LEADLENS_V2_DATABASE_URL overrides any .env DATABASE_URL)
LEADLENS_V2_DATABASE_URL="postgresql://…demo…" python -m alembic upgrade head
LEADLENS_V2_DATABASE_URL="postgresql://…demo…" python -m alembic current      # must show the head revision

# 2. Dry run (writes nothing) — read what it says
LEADLENS_DEMO_DATABASE_URL="postgresql://…demo…" python scripts/seed_demo.py

# 3. Seed for real: you must type the database host yourself
LEADLENS_DEMO_DATABASE_URL="postgresql://…demo…" python scripts/seed_demo.py --apply --confirm-host <demo-db-host>
```

### 3.4 Create the `demo` release branch
The demo app should track a dedicated branch so an unreviewed push to `master`
or `client-1` can never go public. Create it from the reviewed commit and only
fast-forward it deliberately:

```bash
git checkout -b demo <reviewed-commit-or-master>
git push origin demo
```

### 3.5 Create the second Streamlit app
- [ ] Streamlit Community Cloud → **Create app** → repository
  `viralwaghela/LeadLens-AI`, branch **`demo`**, main file **`app.py`**.
- [ ] Pick its own subdomain (e.g. `leadlens-demo`).
- [ ] **Advanced settings → Secrets:** paste `demo/streamlit_secrets.example.toml`
  and replace every `PLACEHOLDER` (table below).
- [ ] Deploy. Streamlit reinstalls `requirements.txt` on each deploy.

### 3.6 Verify
- [ ] Open the app: you should land directly in the workspace,
  with no login or landing page, and the workspace with the **Demo Workspace — Sample Data Only** banner.
- [ ] Try to approve an action, export a CSV, upload a file: each is refused.
- [ ] From a clean shell with the *demo* variables only, `python scripts/production_readiness.py`
  must show no FAIL (it runs the same isolation checks the app does).

### 3.7 Live audit against the real deployment
`scripts/audit_demo_deployment.py` attacks the real demo database and environment and
prints PASS/FAIL: it tries to authenticate as the demo user, escape into another tenant,
modify users/settings/integrations (ORM, raw SQL, DDL, the legacy store), invoke external
actions with provider credentials *planted* in the environment, export, upload, and
exceed the LLM caps (counted in a 1999 window and cleaned up). It refuses to attack any
database that is not a seeded, demo-only one. Keep the operator values in a git-ignored
`.env.demo` (never `.env`, never committed).

```bash
python scripts/audit_demo_deployment.py              # everything except a real OpenAI call
python scripts/audit_demo_deployment.py --live-llm   # also one tiny real call through the limiter
```

### Secrets / environment variables for the demo app

| Name | Value | Notes |
|---|---|---|
| `LEADLENS_DEMO_MODE` | `1` | Turns on every demo guard. **Critical.** |
| `LEADLENS_V2_AUTH_ENABLED` | `1` | Required. |
| `LEADLENS_V2_TENANT_CONTEXT_ENABLED` | `1` | Required. |
| `LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED` | `1` | Required. |
| `LEADLENS_V2_JARVIS_MEMORY_TENANT_AUTHORITATIVE_ENABLED` | `1` | Required. |
| `LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED` | `1` | Required. |
| `LEADLENS_DEFAULT_ORG_SLUG` | `leadlens-demo` | Required, exactly this. |
| `DATABASE_URL` | demo Postgres, plain `postgresql://…` | **Demo database only.** |
| `OPENAI_API_KEY` | key from the demo OpenAI Project | **Demo-only key.** |
| `DEMO_OPENAI_MODEL` | e.g. `gpt-5-mini` | Cheapest suitable model; I could not verify current pricing. |
| `DEMO_LLM_MAX_CALLS_PER_SESSION` | e.g. `15` | Calls, not questions (one question ≈ 3–6 calls). |
| `DEMO_LLM_MAX_CALLS_PER_HOUR` | e.g. `60` | Global. |
| `DEMO_LLM_MAX_CALLS_PER_DAY` | e.g. `300` | Global. |
| `DEMO_LLM_MAX_OUTPUT_TOKENS` | e.g. `1500` | Do not go below 1500 (reasoning models need headroom). |
| `LEADLENS_V2_AUTH_SESSION_SECRET` | random 64 hex chars | `python -c "import secrets; print(secrets.token_hex(32))"` |

**Must NOT be present** (the app refuses to start if any is): `WHATSAPP_*`,
`GMAIL_*`, `GOOGLE_*`, `MASTER_*`, `CLIENT*_`, `LEADLENS_CREDENTIAL_ENCRYPTION_KEY`,
`APP_PASSWORD`, `APP_PASSWORD_RECEPTIONIST`, `APP_USER_ID_RECEPTIONIST`,
`LEADLENS_INTEGRATION_ENV_FALLBACK_ENABLED`, `LEADLENS_V2_SCHEDULER_MULTI_ORG_ENABLED`.

## 4. Reset

Restores the demo tenant to its original seeded state. **Only the demo tenant.**

```bash
LEADLENS_DEMO_DATABASE_URL="postgresql://…demo…" python scripts/reset_demo.py                       # dry run
LEADLENS_DEMO_DATABASE_URL="postgresql://…demo…" python scripts/reset_demo.py --apply --confirm-host <demo-db-host>
```

Safety: dedicated variable only (ambient `DATABASE_URL`/`.env` are ignored and
overridden); Postgres-only; refuses if any provider secret is in the shell; default
dry run; `--apply` needs the typed host; refuses if migrations are not at head; and
**refuses outright — before writing anything — if the database contains any non-demo
organization or the legacy company profile is not the demo's**, i.e. anything that
looks like production. The LLM usage counters are deliberately **not** reset.

The data is anchored to the day it is seeded, so a demo left alone for weeks starts
to look dated. Reset periodically. (The read-only `demo/seed/*.json` history files are
regenerated by the same command; commit them only if you want their dates refreshed.)

## 5. Anything that could still create a path from the demo to production

| Path | Status |
|---|---|
| Operator pastes a production value into the demo secrets | Tripwire refuses to boot on provider secrets, a non-plain-Postgres URL, or any non-demo org / unmarked company profile. It cannot catch every mistake — the checklist above is the primary control. |
| Shared GitHub repo/Actions (the production scheduler runs `master` code with `MASTER_*`/`CLIENT1_*` secrets) | Every demo behaviour is inert unless `LEADLENS_DEMO_MODE=1`; tests prove the normal paths are unchanged. **Residual:** a future regression in that gating would affect the scheduler job. No workflow receives demo secrets. |
| Unreviewed push going public | The demo app tracks the `demo` branch, promoted deliberately. |
| Repo-tracked runtime files (`data/learning/…`, `data/collaboration/…`) | Contain no personal identifiers (checked), but in demo mode they are never read — the demo reads `demo/seed/*` instead. |
| Default-organization auto-creation | `LEADLENS_DEFAULT_ORG_SLUG=leadlens-demo`, enforced by the tripwire. |
| Adapter fallback to environment credentials | No credentials exist in the demo environment; adapters are forced dry-run. |
| Network egress | Community Cloud has no egress firewall. The controls are: no credentials to use, and code guards. **Not fixable at the infrastructure level on this host.** |
| OpenAI spend / abuse | App limits are the second layer; the OpenAI Project budget you set (3.2) is the hard one. |
| Raw-SQL writes bypassing the ORM guard | The guard is at the engine, below the ORM, and covers `text()` too. `core/memory.py` (DB-API) is guarded separately. An optional least-privilege DB role for the app (SELECT everywhere, INSERT/UPDATE on `demo_usage`) would add a database-level layer; it is **untested** here, and `core/memory.py` runs `CREATE TABLE IF NOT EXISTS` on connect, so verify on staging before relying on it. |
| Production schema | The migration adds `organizations.is_demo`, the `DEMO_VIEWER` enum value and `demo_usage` — additive only. Apply it to a production database **before** deploying this code to it (the `Organization` model now selects `is_demo`). Keep this work off `client-1` until you have planned that. |

## 6. Tests

- `tests/test_phase10_demo_guards.py` — the switchboard, the write guard classifier and engine, permissions/role/cap,
  legacy-store guard, adapters, LLM limiter, exports/uploads, file writes, tripwire, and source-level invariants
  (no app code enters the operator context; the outbound-call inventory is exact).
- `tests/test_phase10_demo_seed.py` — dataset properties (volumes, determinism, only reserved-fiction identifiers),
  identity, seeding, idempotency, reset (demo tenant only), and every operator-CLI safety check.
- `tests/test_phase10_demo_app.py` — the **real `app.py`, headless**: landing → entry → banner; forged sessions;
  every page of both workspaces; **clicking every button on every page changes nothing**; Ask Jarvis is
  model-pinned, tool-less and budgeted; no provider secret ever reaches a page.
- `tests/test_phase10_demo_adversarial.py` — every mutating entry point refused **with RBAC completely
  bypassed** (each paired with a control proving the call itself is valid), privilege-escalation attempts,
  no-network adapters, health/readiness integration, and the shipped secrets template.

## 7. Rollback

The demo is a separate app: delete it and its database and nothing else is affected.
In code, unset `LEADLENS_DEMO_MODE` and every demo path becomes inert.
