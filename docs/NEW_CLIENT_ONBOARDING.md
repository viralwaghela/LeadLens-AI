# Onboarding a new client

How to stand up a new, fully separate deployment for a new clinic — own
git branch, own database, own Streamlit app, own WhatsApp number if
they're using that. This is the pattern used for `client-1` (Beyond
Pain), repeated here so the next one takes an hour, not a day of
re-deriving each step.

**Why a separate branch/app/database per client, instead of one shared
app serving everyone?** Real multi-tenancy (one app, many clinics,
isolated data) is the long-term goal, but it's a genuine architectural
rebuild — `core/memory.py` currently holds exactly one business's data at
a time — and is *not* something to back into accidentally while trying
to onboard a client quickly. See `CLAUDE.md`'s "known gaps" section. This
runbook is the deliberate, scoped alternative until that rebuild happens:
each client gets their own isolated everything, and the only thing that
scales is how fast you can repeat the setup.

**Time estimate:** roughly 30–45 minutes once you've done it once,
mostly spent in the Streamlit Cloud and Supabase web UIs (neither has a
scriptable API worth automating for a handful of clients).

---

## 1. Create the client's branch

```bash
git checkout master
git pull
git checkout -b client-2   # pick the next number, or a short client name
git push -u origin client-2
```

Branch from `master`, not an existing client branch — `master` always has
the latest fixes; a client branch may have client-specific tweaks you
don't want to carry over.

## 2. Create a dedicated Supabase project (the client's own database)

1. [supabase.com](https://supabase.com) → **New project**.
2. Name it something identifiable, e.g. `leadlens-client-2`.
3. Pick a strong database password and save it somewhere durable (a
   password manager, not a chat message) — you'll need it in the
   connection string below.
4. Once it's provisioned: **Project Settings → Database → Connection
   string**. Use the **URI** format, either the direct connection
   (`db.<project-ref>.supabase.co`) or the pooler host — both work fine
   (see the note in `.env.example` about this; there was never a real
   pooler bug, just an earlier testing mistake).
5. Substitute in the real password you set in step 3 — Supabase's copy
   button leaves a placeholder.

You now have a `DATABASE_URL` that belongs to this client alone. Existing
data is migrated in automatically the first time the app connects — but
for a new client this is a brand-new empty database, so it just starts
fresh through the onboarding flow.

## 3. Create the Streamlit Cloud app

1. [share.streamlit.io](https://share.streamlit.io) → **New app**.
2. Repository: this repo. **Branch: `client-2`** (the one from step 1) —
   this is the field people miss; picking `master` here silently deploys
   the wrong code.
3. Main file path: `app.py`.
4. Pick a subdomain for the app URL (e.g. `client2-jarvis`).
5. Don't deploy yet if the box lets you add secrets first — otherwise
   deploy once, then immediately go to **Settings → Secrets** before
   sharing the URL with anyone.

## 4. Set the app's secrets

In that app's **Settings → Secrets**, paste (filling in real values):

```toml
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-5-mini"
OPENAI_FAST_MODEL = "gpt-5-mini"

APP_USER_ID = "client-name-or-clinic-name"
APP_PASSWORD = "a-strong-password"

# Optional receptionist login — only add both if the client wants a
# CRM-only login for front-desk staff.
# APP_USER_ID_RECEPTIONIST = "reception"
# APP_PASSWORD_RECEPTIONIST = "a-different-strong-password"

DATABASE_URL = "postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres"

APP_URL = "https://client2-jarvis.streamlit.app"
```

Add WhatsApp credentials too (`WHATSAPP_ACCESS_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_API_VERSION`) once the client has
their own Meta Business number connected — leave blank until then; the
app runs in safe dry-run mode without them.

Never reuse another client's `OPENAI_API_KEY` or `DATABASE_URL` here —
each deployment needs its own, or you'll end up billing one client's
usage to another's key, or worse, mixing two clinics' patient data in one
database.

## 5. Wire up the scheduler (WhatsApp reminders, alerts)

Streamlit Cloud has no background-job support, so nothing here runs
without a scheduled job. Copy the existing pattern:

```bash
cp .github/workflows/scheduler-client-1.yml .github/workflows/scheduler-client-2.yml
```

Then edit the new file:
- `name:` → `Scheduler (client-2)`
- `ref: client-1` → `ref: client-2`
- the cron minute offset (e.g. `"15 * * * *"` instead of `"5 * * * *"`) —
  purely to avoid every client's job firing in the same minute, not a
  hard requirement
- every `secrets.CLIENT1_...` → `secrets.CLIENT2_...`

Commit and push that file on `master` (scheduled workflow triggers are
only read from the repo's default branch, even though the job itself
checks out and runs against the client's branch — see the comment at the
top of `scheduler-master.yml`).

Then add the matching repo-level secrets: GitHub repo → **Settings →
Secrets and variables → Actions** → New repository secret, for each of
`CLIENT2_DATABASE_URL`, `CLIENT2_WHATSAPP_ACCESS_TOKEN`,
`CLIENT2_WHATSAPP_PHONE_NUMBER_ID`, `CLIENT2_WHATSAPP_API_VERSION` (same
values as the Streamlit secrets above).

## 6. Verify

- [ ] App loads and shows the login screen (User ID + password, not the
      old password-only screen — if you see the old screen, the app
      deployed from the wrong branch or needs a manual reboot).
- [ ] Logging in with the new `APP_USER_ID`/`APP_PASSWORD` works.
- [ ] The onboarding flow appears (fresh database → no company profile
      yet) and can be completed.
- [ ] Jarvis mode loads without an OpenAI error.
- [ ] GitHub Actions → the new scheduler workflow → **Run workflow**
      (manual trigger) completes without error.

## 7. Keeping this client's branch updated later

Every fix or feature that should reach every client gets built on
`master` first, tested, then merged forward — same pattern used for
`client-1`:

```bash
git checkout client-2
git merge master --ff-only   # or `git merge master` if it's diverged
                              # (e.g. the client's own devcontainer/config
                              # commits) — a real merge, not a rebase
git push
```

Do this for every client branch after a `master` change goes out, unless
a fix is deliberately meant for one client only.
