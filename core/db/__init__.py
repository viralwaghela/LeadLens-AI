"""V2 Phase 0 relational schema foundation — additive infrastructure only.

Nothing in the live app (app.py, dashboard.py, services/, ui/) imports
anything from this package. It exists so a future phase can migrate onto
it deliberately; importing it here does not change any current behavior.
See docs/V2_COEXISTENCE.md before wiring anything in core/db/ into a live
read or write path.
"""
