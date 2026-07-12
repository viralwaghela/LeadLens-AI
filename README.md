# LeadLens AI

LeadLens is an AI business operating system for small businesses. It gives an owner a calm executive workspace backed by specialized AI departments for Marketing, Sales, Finance, HR and Operations.

## What it does

- Creates a daily AI COO briefing from company memory
- Converts owner updates into tasks, decisions, approvals, risks and opportunities
- Generates complete department deliverables and stores their history
- Exports business documents in DOCX/XLSX/TXT formats
- Provides an executive home with company health, financial snapshot and approvals
- Lets the owner ask questions grounded in the company's saved memory

## Departments

- **Marketing:** strategy, content calendar, reels, captions, ads and image prompts
- **Sales:** prospecting strategy, cold emails, WhatsApp outreach, call scripts and proposals
- **Finance:** expense review, budgets, cash flow, profitability and forecasts
- **HR:** job descriptions, interview packs, onboarding and performance reviews
- **Operations:** daily plans, task assignments, bottlenecks, risks and weekly reports

## Tech stack

- Python
- Streamlit
- OpenRouter API
- JSON-based business memory
- python-docx and openpyxl

## Local setup

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m streamlit run app.py
```

Add your OpenRouter key to `.env` before using AI features.

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
- The bundled JSON database is suitable for a local demo, not multi-user production.
- Authentication, hosted persistence and role permissions belong in the deployment roadmap.

## Current status

LeadLens v1 includes the complete local executive workflow and all five departments. The next production step is deployment hardening: authentication, a hosted database, background jobs and integrations.
