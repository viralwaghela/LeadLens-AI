# OpenAI Jarvis Update

This build migrates LeadLens from OpenRouter and deterministic-only Jarvis
answers to the official OpenAI Responses API.

## Updated

- One shared OpenAI connector now serves every AI feature.
- Jarvis answers are grounded in the current LeadLens business memory.
- A dedicated context builder now includes privacy-safe aggregates for
  patients, appointments, packages, payments, therapists, leads and
  corporate clients.
- Every AI context includes a source register, record counts and freshness
  timestamps.
- Confirmed facts, derived signals and unavailable sources are separated.
- Jarvis is instructed to tag business facts with their LeadLens source.
- The Jarvis screen includes a collapsible data-grounding audit.
- Recent conversation context is included in Jarvis follow-up questions.
- Management questions are routed to Sales, Marketing, Finance, Operations,
  HR, Customer Success and Analytics specialists as relevant.
- Each specialist receives a scoped evidence bundle from allowlisted,
  read-only tools.
- Jarvis synthesises the specialist findings into one management answer.
- The Chief-of-Staff UI displays the agents and read-only tools consulted.
- Recorded channel preferences are not presented as connected integrations,
  and recorded automation preferences are not presented as runtime authority.
- Only whitelisted business fields are sent to the model.
- Responses are requested with `store=False`.
- External actions remain approval-first.
- If the API is unavailable, Jarvis labels its local fallback clearly.
- The Integrations page reports whether `OPENAI_API_KEY` is configured.

## Private configuration

Create `.env` from `.env.example`, then add:

```env
OPENAI_API_KEY=your_real_key_here
OPENAI_MODEL=gpt-5.1
OPENAI_FAST_MODEL=gpt-5-mini
OPENAI_STORE_RESPONSES=false
APP_URL=http://localhost:8501
```

Do not place the real key in this file, `.env.example`, Python source,
screenshots, or GitHub.

## Verification

Run:

```powershell
python -m compileall services ui agents
python test_jarvis_context.py
python test_specialist_orchestration.py
python -m streamlit run app.py
```

In Jarvis Mode, test:

- `How are sales doing?`
- `Can we afford another therapist?`
- `Why are bookings falling?`
- `Give me a complete business review.`

Open **Consultation trace** beneath the answer. It should list the relevant
specialists, show read-only tools and confirm that no external action ran.
