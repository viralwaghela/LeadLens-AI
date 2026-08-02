# Approval-first actions

Jarvis can now turn a tracked recommendation into a prepared Gmail, WhatsApp,
or Google Calendar action without executing it immediately.

## Safety sequence

1. Track a Jarvis recommendation.
2. Prepare an action and review its exact payload.
3. Open **Action Center → Prepared actions**.
4. Approve or reject the stored payload.
5. If approved, click **Execute approved action** as a separate final step.
6. LeadLens records the verified adapter result and links it to Jarvis memory.

Rejected actions cannot run. Unapproved actions are blocked. Repeated Execute
clicks do not repeat an action that already reached a terminal state.

## Local testing

With external credentials absent, all adapters use safe simulation mode:

```powershell
python test_approval_actions.py
python test_jarvis_memory.py
python test_jarvis_context.py
python test_specialist_orchestration.py
python -m compileall -q .
python -m streamlit run app.py
```

Simulation validates the full preparation, approval, execution, audit, and
memory flow without sending a message or creating a calendar event.
