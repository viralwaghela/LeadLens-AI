# LeadLens review notes

## Changes applied

- Replaced the crowded 12-tab navigation with a calmer sidebar-based product flow.
- Added a redesigned executive home with financial metrics, briefings, approvals and an AI question box.
- Added working Approve/Reject actions for pending approvals.
- Added duplicate protection for open tasks and approvals.
- Added robust LLM JSON parsing, including fenced JSON responses.
- Hardened AI request handling and made the model configurable from `.env`.
- Made business-memory paths independent of the terminal working directory.
- Added atomic JSON writes and corrupted-database recovery.
- Added missing Finance, HR and Operations sections to the default memory schema.
- Added `requirements.txt`, `.env.example`, an updated README and a stronger `.gitignore`.

## Validation performed

- Python compilation completed successfully for the full project.
- A live Streamlit launch could not be performed in the review container because Streamlit is not installed there.

## Run locally

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m streamlit run app.py
```

## V1 approval fix
- Approval actions now persist reliably.
- Duplicate pending approvals with the same title and department are resolved together.
- Approval decisions generate activity and notification records.
- Exact-title approval tasks are completed automatically when approved.
