"""V2 Phase 1 — identity, authorization, and membership backend services.

Additive-only, dormant infrastructure, exactly like core/db/ in Phase 0.
Nothing in the live app (app.py, dashboard.py, core/auth.py, ui/,
services/, scheduler/) imports anything from this package. It exists so
a later, explicit, discussed phase has a tested identity/authorization
backend to migrate onto — see docs/V2_PHASE1_IDENTITY.md for the full
architecture and docs/V2_COEXISTENCE.md for the broader V2 coexistence
rule this package follows.

core/auth.py remains the only real login gate for the live application.
"""
