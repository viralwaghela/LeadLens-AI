# LeadLens AI

LeadLens is an AI business operating system for small businesses. It gives an owner a calm executive workspace backed by specialized AI departments for Marketing, Sales, Finance, HR and Operations.

## What it does

- Creates a daily AI COO briefing from company memory
- Converts owner updates into tasks, decisions, approvals, risks and opportunities
- Generates complete department deliverables and stores their history
- Exports business documents in DOCX/XLSX/TXT formats
- Provides an executive home with company health, financial snapshot and approvals
- Lets the owner ask questions grounded in the company's saved memory
- Audits which business and clinic sources informed Jarvis's answer
- Routes management questions to relevant specialist AI agents
- Gives each specialist only the read-only evidence required for its role
- Shows which agents and data tools were consulted before Jarvis synthesised
  the final answer
- Retrieves relevant owner preferences, tracked recommendations and measured
  outcomes before each consultation
- Learns from outcomes only after the owner explicitly tracks a recommendation
  and submits its result
- Maintains a linked clinic CRM for patients, appointments, treatment packages,
  payments and therapists
- Calculates patient inactivity, renewal, consent and follow-up signals from
  real CRM records
- Prepares consent-aware clinic workflows behind an approval gate; no message
  is sent automatically

## Clinic CRM

The local clinic CRM is available from **Departments → Patient Records** and
supports:

- Searchable patient directory with status and consent tracking
- Linked patient profiles with appointments, packages and payments
- Appointment scheduling and status updates
- Package assignment, session balances and renewal signals
- Payment recording and pending-payment totals
- Therapist capacity records
- Stable record IDs, relationship validation and soft archiving
- Privacy-safe aggregate CRM context for Jarvis and specialist agents

The CRM deliberately avoids detailed clinical notes in this local MVP. Patient
identities and contact details remain outside LLM prompts.

## Departments

- **Marketing:** strategy, content calendar, reels, captions, ads and image prompts
- **Sales:** prospecting strategy, cold emails, WhatsApp outreach, call scripts and proposals
- **Finance:** expense review, budgets, cash flow, profitability and forecasts
- **HR:** job descriptions, interview packs, onboarding and performance reviews
- **Operations:** daily plans, task assignments, bottlenecks, risks and weekly reports

## Tech stack

- Python
- Streamlit
- Official OpenAI Responses API
- JSON-based business memory
- Atomic, structured Jarvis learning memory with outcome-linked recommendations
- Privacy-safe clinic context aggregation with source provenance
- Read-only specialist-agent orchestration and consultation traces
- python-docx and openpyxl

## Local setup

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m streamlit run app.py
```

Add your OpenAI API key to `.env` before using AI features:

```env
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5.1
OPENAI_FAST_MODEL=gpt-5-mini
OPENAI_STORE_RESPONSES=false
```

Never add the real key to `.env.example`, source code or GitHub.

Run the CRM regression test from the project directory:

```powershell
python test_clinic_crm.py
```

## Project structure

```text
agents/       Agent registration and routing
core/         Business memory, activity and notifications
coo/          COO planning and business-health logic
executive/    Executive-home metrics and summaries
marketing/    Marketing generation and exporters
sales/        Sales generation and exporters
finance/      Finance generation and exporters
hr/           HR generation and exporters
operations/   Operations generation and exporters
ui/           Department workspaces
services/     AI and JSON utilities
database/     Local business memory
generated/    Generated deliverables
```

## Security notes

- Never commit `.env` or API keys.
- Patient and therapist names, contact details and clinical notes are excluded
  from the Jarvis LLM context; the model receives aggregate clinic signals.
- Patient records are archived rather than permanently deleted from the UI.
- Clinic outreach candidates require recorded consent and still create an
  approval request before any external execution.
- The bundled JSON database is suitable for a local demo, not multi-user production.
- Specialist tools in this build are read-only. Recorded owner preferences are
  not treated as runtime permission, and listed channels are not treated as
  connected integrations.
- Normal conversations do not silently become permanent memory. Tracking a
  recommendation and recording its outcome are explicit owner actions.
- Authentication, hosted persistence and role permissions belong in the deployment roadmap.

## Current status

LeadLens v1 includes the complete local executive workflow, all five
departments, official OpenAI reasoning, a source-aware context layer, real
read-only specialist-agent consultation, relevant-memory retrieval, explicit
outcome learning, approval-gated action preparation and a linked clinic CRM.
Production deployment still requires authentication, role-based access,
encrypted hosted persistence, background jobs, backups and hardened live
integrations.
