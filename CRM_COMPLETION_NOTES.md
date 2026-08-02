# LeadLens CareOS CRM Completion

## Included

- Linked patient, therapist, appointment, package, payment, and progress records
- Stable record IDs and relationship validation
- Patient search, directory, profile, editing, and safe archiving
- Appointment, package, payment, and therapist management
- Consent-aware patient records
- CRM risk signals for inactivity, renewals, and pending payments
- Privacy-safe CRM summaries supplied to Jarvis
- Approval-first workflow preparation using eligible CRM records
- Automated CRM lifecycle and relationship tests

## Safety boundaries

- The CRM stores structured care-progress updates, not diagnoses or complete
  free-form medical records.
- Care-progress updates stay local and are excluded from Jarvis LLM context.
- External messages and actions remain approval-gated.
- Archived records are preserved for auditability instead of being deleted.
- The packaged project excludes `.env`, virtual environments, caches, and API keys.

## Verification

Run from the `LeadLens_CareOS` directory:

```powershell
python -m pip install -r requirements.txt
python test_clinic_crm.py
python test_jarvis_context.py
python test_jarvis_memory.py
python test_specialist_orchestration.py
python test_approval_actions.py
python -m compileall -q .
python -m streamlit run app.py
```

## Production boundary

This release completes the functional local CRM milestone. A production
multi-user deployment still needs authenticated accounts, an encrypted hosted
database, backups, access policies, retention controls, and a formal privacy
review.
