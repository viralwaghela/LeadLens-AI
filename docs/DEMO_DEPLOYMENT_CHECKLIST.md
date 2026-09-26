# LeadLens public demo — final deployment checklist

Status key: **PASS** = verified (evidence in Notes). **PENDING** = needs the real demo
resources. **FAIL** = ran and failed. Items marked **[CRITICAL]** must all be PASS before the
demo is called ready. Nothing here may be run from a checkout that has a `.env`.

Run every command from the clean worktree: `git worktree add ../LeadLens-demo-ops demo-environment`
with demo values loaded from a gitignored `.env.demo` (never committed, never echoed).

| # | Area | Action / command | Expected result | Status |
|---|---|---|---|---|
| 1 | **Git branch** [CRITICAL] | `git log --oneline master..demo-environment` and `git diff --stat master origin/master` | Demo work only on `demo-environment` (4 commits: 59e0d0f, 159da04, c1dc6b7, 9947609); `master`=d4d6c5c and `client-1`=d576b6f unchanged | PASS (local) |
| 2 | Git: no secrets | `git grep -InE "sk-[A-Za-z0-9]{20,}\|-----BEGIN"` ; `git ls-files .env .env.demo` | No matches; no env files tracked | PASS (CI pattern scan + pre-commit hook) |
| 3 | Git: release branch | `git branch demo demo-environment && git push origin demo` (you push) | `git ls-remote origin demo` shows the commit | PENDING |
| 4 | **Demo Streamlit app** [CRITICAL] | New app, repo, branch `demo`, main file `app.py`, secrets from `demo/streamlit_secrets.example.toml` | App boots to the demo entry page, not a login form; production app URL untouched | PENDING |
| 5 | **Separate Postgres** [CRITICAL] | New Neon/Supabase *project*; compare host with production `DATABASE_URL` host | Different host and project; URL is plain `postgresql://…?sslmode=require`; `python scripts/health_check.py` reports DB reachable | PENDING |
| 6 | **Separate OpenAI project/key** [CRITICAL] | New Project `leadlens-demo`, restricted key, monthly budget $10–15 with 50/80% alerts, model allowlist `gpt-5-mini`; key differs from production key | Dashboard shows the key under the demo project only; production key not in demo secrets | PENDING |
| 7 | **Environment variables** [CRITICAL] | Set the 15 variables listed in `demo/streamlit_secrets.example.toml`; `python -c "from core.demo_tripwire import environment_problems as e; print(e())"` in the demo env | `[]`. None of WHATSAPP_*, GMAIL_*, GOOGLE_*, MASTER_*, CLIENT*_*, LEADLENS_CREDENTIAL_ENCRYPTION_KEY, APP_PASSWORD* set | PENDING (tripwire logic PASS in tests) |
| 8 | **Migrations** [CRITICAL] | `python -m alembic upgrade head` then `python -m alembic current` and `python -m alembic check` | Head is `2918a71a44ce`; `check` reports no drift; enum value `DEMO_VIEWER` present | PENDING on real Postgres (PASS on SQLite; upgrade/downgrade cycle PASS) |
| 9 | **Seed** [CRITICAL] | `python scripts/seed_demo.py` (dry run) then `python scripts/seed_demo.py --apply --confirm-host <host>` | Dry run lists counts and writes nothing; apply creates one org `leadlens-demo` (is_demo), synthetic patients/appointments/leads/finance, viewer user; re-run is idempotent | PENDING on real Postgres (PASS on SQLite) |
| 10 | **Reset** | `python scripts/reset_demo.py` then `--apply --confirm-host <host>` | Only the demo tenant is wiped and reseeded; wrong host string refused; refuses if any non-demo organization exists | PENDING on real Postgres (PASS on SQLite) |
| 11 | **Demo login** [CRITICAL] | Open demo URL, click enter | Enters as `DEMO_VIEWER` with no password; no path to a shared-password or email login form; cannot choose another organization | PENDING (AppTest PASS) |
| 12 | **Tenant isolation** [CRITICAL] | `python scripts/audit_demo_deployment.py` (tenant checks) and `python scripts/verify_multi_org_readiness.py` | Exactly one organization exists and it is the demo; no production data visible; audit aborts if a non-demo org is present | PENDING on real Postgres (PASS in-process) |
| 13 | **External-action blocking** [CRITICAL] | Audit external-action checks; in the UI try approving/executing an action, sending WhatsApp/Gmail, booking calendar | Every path refused or dry-run; no outbound HTTP; adapters have no credentials | PENDING on real Postgres (PASS in-process) |
| 14 | **Rate / spend limits** [CRITICAL] | `python scripts/audit_demo_deployment.py --live-llm`; check OpenAI dashboard usage | Per-session (12), hourly (50) and daily (200) caps refuse further calls with a friendly message; wrong-model requests pinned; usage stays within budget | PENDING live (limiter PASS in-process, fails closed) |
| 15 | **Secrets exposure** [CRITICAL] | Audit secret checks; then in the UI ask Jarvis to print keys/env; grep app pages and logs for key fragments | No secret value in any page, error box, Jarvis reply or log; planted secrets never printed | PENDING live (PASS in-process) |
| 16 | **Adversarial tests** [CRITICAL] | `python -m pytest tests/test_phase10_demo_adversarial.py tests/test_phase10_demo_audit.py -q` then the live audit above | All pass; audit prints `DEPLOYMENT AUDIT: PASS`. Covers prod-data access, permission bypass, external actions, secrets, user/settings/integration edits, export/upload, tenant escape, LLM overrun | PASS in-process; live audit PENDING |
| 17 | Full suite | `python -m pytest tests/ -q` plus script-style tests per `.github/workflows/ci.yml` | All green | PARTIAL: demo files 139/139 after last fix; full suite not re-run since final edits — re-run before sign-off |
| 18 | **Final smoke test** [CRITICAL] | Open deployed URL in a fresh browser: enter demo, browse dashboard, patients, appointments, finance, ask Jarvis 2 questions, try a write and an export | Pages load with seeded data; Jarvis answers; writes and exports show the "demo is read-only" message; no tracebacks | PENDING |
| 19 | Rollback | Delete or pause the demo Streamlit app; rotate/delete the OpenAI demo key | Production app unaffected | PENDING |

## Sign-off rule

The demo is **NOT READY** until every **[CRITICAL]** row is PASS on the real demo
resources. Current state: in-process evidence is PASS; real-deployment evidence is PENDING.
Do not merge `demo-environment` into `master`/`main`, and do not point production at it.
