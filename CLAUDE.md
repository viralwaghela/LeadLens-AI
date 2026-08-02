# LeadLens CareOS — Project Context

Read this before making changes. This explains what the product is actually
meant to be, not just what the code currently does — there's a gap between
the two in places, and it's important to know which is which.

## The vision

LeadLens is not a one-off CRM built for a single clinic. It's a product
meant to serve small-scale clinics, fitness centers, and similar small
service businesses broadly. "Beyond Pain" (a physiotherapy clinic in Malad,
Mumbai) is the founder's own business and the current example/demo data —
it is not the product's identity, and code should not assume it's the only
customer this will ever have.

**The CRM is not the product. Jarvis is the product.**

The actual thing being sold is an AI employee — Jarvis — who has a team of
his own and can be asked to do anything to help the business grow. The
name is a deliberate reference to Tony Stark's Jarvis: not just a capable
assistant, but a loyal one. The founder wants Jarvis to feel like the best
friend of the business — someone who only ever has the business's best
interests in mind, nothing else. This is a tone and trust design goal as
much as a technical one: how Jarvis phrases things, what he proactively
raises versus stays quiet on, how he owns mistakes, should all reflect
"I'm on your side," not "I'm a tool you're operating."

Practically, Jarvis's job is to take over tasks that are boring, repetitive,
or don't require human creativity or judgment — freeing the business owner
to spend their attention only on things that genuinely need a human. The
CRM (patient records, tasks, approvals, daily logs) exists as the substrate
Jarvis needs in order to actually do things for the business, not as a
feature in its own right.

## How the current code maps to that vision

- `services/specialist_orchestration.py` and the "agent team" — this is
  Jarvis's *staff*, not just a multi-agent implementation detail. Different
  specialists get consulted and their input synthesized into one voice:
  Jarvis's.
- `services/jarvis_context.py` — builds a privacy-filtered, grounded view
  of the real business for Jarvis to reason over, so he's never just
  making things up about the business.
- `services/integration_manager_v21.py` — the approval gate. This is the
  mechanism for Jarvis to actually *act* in the world (send a message,
  book something) rather than only talk, currently implemented
  conservatively: nothing external fires without a human approving it
  first. This is the literal implementation of "executes tasks that don't
  need human attention," deliberately gated for trust/safety right now.
- `integrations/calendar_service.py`, `gmail_service.py`,
  `whatsapp_service.py` — the actual hands Jarvis uses to act, with real
  dry-run and live modes.
- `core/memory.py` — the single source of truth for one business's data.
  Supports either local SQLite or an external Postgres database
  (Supabase/Neon) via a `DATABASE_URL` env var, chosen so the app's data
  survives regardless of what happens to the app's hosting.

## Where the code does NOT yet match the vision — known gaps

1. **Single-tenant, not multi-tenant.** The app currently holds exactly one
   business's data at a time (`core/memory.py` is built around one
   `memory_store` row). "One LeadLens, many clinics" is the real long-term
   product, but that requires a genuine multi-tenant rebuild — separate,
   isolated data per clinic — not a small tweak. Don't assume this is
   solved; don't quietly add multi-tenant-shaped code without discussing
   it first, since it's a significant architectural decision.
2. **Jarvis's personality hasn't been deliberately written yet.** The
   "best friend, only thinks about your business" character is a real
   design target that likely isn't reflected yet in the actual prompting
   in `services/ai.py` / `services/specialist_orchestration.py` — treat
   this as an open, valuable piece of work, not something already done.
3. **"Beyond Pain" specificity.** Some code/data may implicitly assume this
   one clinic. Flag anything that hardcodes assumptions specific to
   Beyond Pain rather than being generic across clinic types.

## Recent history (for context, not action needed)

- The UI was rebuilt to match a "Mission Control" design (top bar, hero,
  metric cards, agent status panels, approval queue, activity feed).
- A bug causing Jarvis to fall back to templated/canned responses was
  fixed (silent error swallowing + a reasoning-model token budget issue).
- Storage was migrated from a hand-rolled JSON file to SQLite, then
  extended to optionally use Postgres (Supabase/Neon) via `DATABASE_URL`.
- The codebase was just cleaned up: ~130 dead files from earlier "Phase
  1–23" development (abandoned `sales/`, `finance/`, `hr/`, `marketing/`,
  `operations/`, `coo/`, `executive/`, `agents/` packages and their UI
  counterparts, none of which were ever actually wired into `app.py`) were
  removed. Only files reachable from `app.py` remain, plus a `tests/`
  folder with genuine regression tests for the live code. Two real bugs
  surfaced by those tests were fixed along the way (in
  `services/learning_memory_v22.py` and
  `services/agent_collaboration_v23.py`).

## Automation roadmap

See `docs/AUTOMATION_ROADMAP.md` for the phased build order for Jarvis's
autonomous automations (scheduler foundation, then risk-tiered rollout).
Follow it in order — do not start a later phase before the current one is
built and tested. If asked to "add an automation," check which phase it
belongs to and confirm with the founder before skipping ahead.

## Working style

- Confirm before big architectural decisions (especially anything
  touching multi-tenancy) rather than assuming and building.
- When something in the code contradicts the vision above, flag it rather
  than silently "fixing" it to match your best guess — the founder should
  decide, since some of these are product decisions, not bugs.
