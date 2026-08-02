# LeadLens CareOS — Dual Workspace UI/UX

This release separates LeadLens into two intentional experiences.

## CRM workspace

- Opens by default.
- Focuses only on patient records, appointments, treatment plans, follow-ups,
  payments, clinic staff, and settings.
- Makes the patient directory the primary landing page.
- Supports structured patient progress notes, including progress status, pain
  score, summary, and next step.
- Keeps progress notes local to the CRM and out of the Jarvis LLM context.
- Provides a plain-language dashboard and Jarvis tip of the day.
- Moves all clinic-record downloads into the Dashboard.

## Jarvis workspace

- Uses a dedicated cinematic dark navy and electric-blue interface.
- Includes Mission Control, Patient Intelligence, AI Team, Workflows,
  Integrations, Approvals, and Deployment.
- Keeps the Chief of Staff conversation input at the bottom of Mission Control.
- Preserves approval-first execution, specialist orchestration, outcome
  tracking, and the existing OpenAI configuration.

## Workspace switch

The CRM/Jarvis switch uses one fixed position at the bottom of the sidebar in
both modes. Its location does not change; only the selected state and visual
glow change.

## Safety

- No `.env` file or API key is included in the release archive.
- External actions remain approval-gated.
- Existing CRM and Jarvis regression tests remain available.
