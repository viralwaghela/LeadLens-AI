# Beyond Pain marketing site

Static HTML/CSS/JS site, plus one serverless function (`api/lead.py`)
that lets the booking form write a new lead directly into LeadLens
CareOS's database — visible immediately on the **Leads** page in the CRM.

## Local preview

```bash
python -m http.server 5500 --directory .
```

(or use the `beyond-pain-site` entry in `.claude/launch.json`). The
booking form's client-side validation works locally, but the actual
submission will fail gracefully (there's no `/api/lead` server here) —
that's expected. Only a real deploy on Vercel has the serverless
function.

## Deploying on Vercel

1. [vercel.com](https://vercel.com) → **New Project** → import this
   GitHub repo (the same one LeadLens CareOS itself lives in).
2. **Root Directory**: set it to `marketing-site` — this is the field
   people miss; leaving it at the repo root will try to deploy the
   Streamlit app instead.
3. Framework preset: **Other** (it's plain static HTML — no build step
   needed).
4. Before the first deploy, add one environment variable in
   **Settings → Environment Variables**:

   ```
   DATABASE_URL = <the exact same Postgres connection string configured
                    as DATABASE_URL in this clinic's Streamlit Cloud
                    app secrets>
   ```

   Using a different database here — or leaving it blank — means leads
   the form captures will either go nowhere the clinic can see, or land
   in the wrong clinic's CRM. Copy it directly from the Streamlit app's
   secrets rather than retyping it.
5. Deploy. Vercel auto-detects `api/lead.py` as a serverless function
   (Python runtime) and serves `index.html`/`styles.css`/`main.js`
   statically — no `vercel.json` needed for this.
6. Point the clinic's real domain at this Vercel project once it's live
   (Vercel's own **Domains** settings), or use the `*.vercel.app` URL
   Vercel assigns for now.

## How a submission actually reaches the CRM

1. Visitor fills the form in the **Contact** section and submits.
2. `main.js` POSTs `{name, phone, email, message}` as JSON to `/api/lead`.
3. `api/lead.py` validates it, connects to `DATABASE_URL`, and inserts a
   new row into the same `clinic_leads` list the main app reads —
   using the same row-lock pattern (`SELECT ... FOR UPDATE`) as
   `core/memory.py`'s own writes, so a form submission can never race
   with the clinic staff using the app at the same moment.
4. It shows up immediately on the **Leads** page in LeadLens CareOS,
   with Source = "Website" and Status = "New" — no redeploy, no
   scheduler run, no delay.

## Spam protection

There's a honeypot field (`website`, hidden via CSS) — a real visitor
never fills it, a bot filling every field on the page usually does.
Submissions with it filled are silently accepted (so the bot doesn't
learn it was rejected) but never actually written to the database.

This is a reasonable baseline for a small clinic's traffic, not a hard
guarantee against spam. If it becomes a real problem, the next step
would be a free CAPTCHA service (e.g. Cloudflare Turnstile) in front of
the form — not built here, since there was no evidence yet that it's
needed.

## Keeping this in sync with the main app

`api/lead.py` is deliberately self-contained rather than importing
`core/memory.py` — those files live outside this Vercel project's root
directory, and relying on Vercel to bundle files from outside its own
project root is fragile. It mirrors the same `memory_store` table shape
and locking pattern by hand. If that schema ever changes in
`core/memory.py` (e.g. a new required column), `api/lead.py`'s
`_insert_lead()` needs to be updated to match, or new leads will start
failing to save.
