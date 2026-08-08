# LeadLens CareOS

LeadLens is an AI-powered Business Operating System for physiotherapy
clinics and similar small service businesses. **Jarvis is the product** —
an AI Chief of Staff who manages the clinic and proactively assists the
owner. The CRM is the substrate underneath him: the patient records,
appointments, packages and payments Jarvis needs in order to actually do
things for the business.

The app has two linked workspaces, toggled from the sidebar via **the
Core** (the single JARVIS/CRM switch):

- **CRM workspace** — patients, appointments, treatment plans, follow-ups,
  a clinic dashboard, payments, clinic team (therapists), and settings.
- **JARVIS workspace** — Mission Control, Patient Intelligence, the AI
  team (specialist agents synthesized into one voice), autonomous
  workflows, integrations, an approval queue, business memory (Data Hub /
  Reports / Memory Center).

"Beyond Pain" (Malad, Mumbai) is the founder's own clinic and the current
demo data — not the product's identity.

## What it does

- Jarvis builds a privacy-filtered, grounded view of the real clinic
  (`services/jarvis_context.py`) and reasons only over that — never makes
  things up about the business.
- A team of specialist AI agents (`services/specialist_orchestration.py`)
  are consulted and synthesized into a single Jarvis voice.
- Every external action (send a message, book something) goes through an
  approval gate (`services/integration_manager_v21.py`) — nothing fires
  without a human approving it first. Calendar, Gmail and WhatsApp
  integrations (`integrations/`) support both dry-run and live modes.
- The CRM tracks patients, appointments, treatment packages, payments and
  therapists, and derives inactivity/renewal/follow-up signals from real
  records.
- Business memory (`core/memory.py`) is the single source of truth for one
  clinic's data — local SQLite by default, or Postgres (Supabase/Neon) via
  `DATABASE_URL` so data survives regardless of what happens to the app's
  hosting.

## Tech stack

- Python, Streamlit
- Official OpenAI Responses API
- SQLite (default) or Postgres via `DATABASE_URL`
- python-docx, openpyxl, pandas for document generation
- Google Calendar / Gmail / WhatsApp integrations (dry-run by default)

## Local setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m streamlit run app.py
```

Then fill in `.env` — see `.env.example` for every variable and what each
one does. At minimum, set `OPENAI_API_KEY` for AI features to work. Set
`APP_PASSWORD` before putting any real clinic data behind a shared URL.

## Running the tests

Install dev dependencies first (adds `pytest` on top of the app's own
requirements):

```powershell
pip install -r requirements-dev.txt
```

Most of `tests/` is pytest-style and runs with:

```powershell
python -m pytest tests/
```

A subset of `tests/` (scheduler and automation checks — `test_scheduler.py`,
`test_memory_locking.py`, `test_appointment_reminder.py`,
`test_capacity_alert.py`, `test_lead_qualification_alert.py`,
`test_low_booking_alert.py`, `test_monthly_business_review.py`,
`test_revenue_monitoring.py`, `test_waiting_list_automation.py`,
`TEST_PHASES_21_TO_23.py`) are standalone scripts rather than pytest
functions. Run each directly from the project root with the root on
`PYTHONPATH`:

```powershell
$env:PYTHONPATH = "."
python tests\test_scheduler.py
```

(Every file under `tests/` imports `tests/_bootstrap.py` first, which
guarantees a test can never accidentally reach the real Postgres database
even if `DATABASE_URL` is set in `.env`.)

## Project structure

```text
app.py            Entry point: login gate, then CRM or onboarding
dashboard.py      Workspace router — the Core switch, CRM/JARVIS nav
onboarding.py     First-run clinic setup
core/             Business memory (SQLite/Postgres), auth
services/         AI connector, specialist orchestration, Jarvis context,
                  integration/approval manager, learning memory
ui/               CRM and JARVIS screens (workspace_theme.py owns the
                  locked Core-switch/theme CSS)
integrations/     Calendar, Gmail, WhatsApp — dry-run and live modes
scheduler/        Background automation checks
workflows/        Autonomous workflow definitions
database/         Local SQLite file and JSON fallbacks (gitignored)
data/             Runtime data: security audit log, collaboration, learning
generated/        Generated documents (gitignored)
tests/            Regression tests for the live code (see above)
docs/             Design specs (e.g. CORE_SWITCH_SPEC.md)
```

## Deployment

The app reads all configuration from environment variables (see
`.env.example`). Set `APP_PASSWORD` and either leave `DATABASE_URL` unset
(local SQLite) or point it at a managed Postgres instance before any real
clinic data goes in — local SQLite does not survive an ephemeral
filesystem (see the note in `.env.example`).

### Streamlit Community Cloud

1. Push this repo to GitHub.
2. Create a new app pointing at `app.py`.
3. In the app's **Secrets**, set `OPENAI_API_KEY`, `APP_USER_ID`,
   `APP_PASSWORD`, and `DATABASE_URL` (required here — the platform's
   filesystem is ephemeral, so local SQLite will not persist between
   restarts).

Onboarding an additional client onto their own separate deployment (own
branch, own database, own app)? See
[`docs/NEW_CLIENT_ONBOARDING.md`](docs/NEW_CLIENT_ONBOARDING.md) instead
of repeating these steps from scratch each time.

### Render / Railway

Both platforms build directly from the included `Dockerfile`.

1. Create a new Web Service from this repo.
2. Build command: none needed (Dockerfile handles it). Start command:
   already set in the Dockerfile's `CMD`.
3. Set the port to `8501`.
4. Add the same environment variables from `.env.example` in the
   platform's dashboard (`OPENAI_API_KEY`, `APP_PASSWORD`, `DATABASE_URL`,
   etc.) — never commit them.
5. Use `DATABASE_URL` (Postgres) rather than local SQLite unless the
   platform gives you a persistent disk mount.

### Docker (self-hosted / local)

```bash
cp .env.example .env   # fill in real values
docker compose up --build
```

This builds from `Dockerfile`, exposes port `8501`, and mounts
`./database`, `./data` and `./generated` as volumes so local SQLite data
and generated documents survive container restarts. Set `DATABASE_URL` in
`.env` instead if you'd rather use hosted Postgres.

### Local Windows

See **Local setup** above — `python -m streamlit run app.py` with local
SQLite is the default and requires no external database.

## Security notes

- Never commit `.env` or API keys — `.gitignore` already excludes `.env`
  and `.streamlit/secrets.toml`.
- Set `APP_PASSWORD` before deploying anywhere with real clinic data — the
  app displays an on-screen warning banner whenever it is left unset.
- Patient and therapist names, contact details and clinical notes are
  excluded from the Jarvis LLM context; the model receives aggregate
  clinic signals only (see `services/jarvis_context.py`).
- Patient records are archived rather than permanently deleted from the
  UI.
- Every external action (WhatsApp, email, calendar) requires an explicit
  approval before execution — nothing fires automatically.
- The app is single-tenant: it holds exactly one clinic's data at a time.
  Multi-tenancy (many clinics on one deployment) is a planned but not yet
  built architecture — do not assume it is supported.

## Current status / known gaps

This build is the local/single-clinic pilot version. Before a wider
rollout, it still needs: authentication beyond a single shared
`APP_PASSWORD`, role-based access, multi-tenant data isolation, and
hardened production monitoring. See `CLAUDE.md` in the repo root for the
full product vision and the gaps between it and the current code.
