"""Public-demo mode — the one dependency-free switchboard every demo guard uses.

The public demo (see docs/V2_DEMO_ENVIRONMENT.md) is a SEPARATE deployment
with its own database, its own secrets and its own LLM key. This module is
the code-level defense in depth on top of that physical isolation: even if
the deployment were misconfigured, demo visitors still could not write,
export, upload, or trigger an external action.

Everything here is inert unless LEADLENS_DEMO_MODE is set, so it changes
nothing for a normal (production or client) deployment.

Deliberately imports nothing from the rest of the app (no sqlalchemy, no
streamlit, no core.memory) so core/memory.py, integrations/*.py and
services/ai.py can all depend on it without any import cycle.

Guard categories and what may bypass them:

    writes     blocked in demo mode unless inside `allow_demo_writes()`,
               which only the operator scripts (seed/reset) and Alembic use.
    external   (email / SMS / WhatsApp / webhooks / calendar / any
               third-party side effect) blocked in demo mode. NOT
               bypassable — not even by the seed/reset scripts.
    export     blocked in demo mode. Not bypassable.
    upload     blocked in demo mode. Not bypassable.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
from pathlib import Path

DEMO_DISABLED_MESSAGE = "This action is disabled in the public demo."
DEMO_ERROR_MESSAGE = "That request could not be completed in the public demo."
DEMO_USAGE_CAP_MESSAGE = (
    "The public demo's AI usage limit has been reached. "
    "Please try again later — the rest of the demo remains available."
)

# Fixed identities for the one demo tenant. The `.invalid` TLD is reserved
# (RFC 2606): this address can never receive real mail.
DEMO_ORG_SLUG = "leadlens-demo"
DEMO_ORG_NAME = "LeadLens Demo Clinic"
DEMO_USER_EMAIL = "demo-viewer@demo.leadlens.invalid"

_TRUE = {"1", "true", "yes"}


class DemoModeError(RuntimeError):
    """Raised when something the public demo forbids is attempted. The
    message is always safe to show to a visitor."""

    def __init__(self, action: str = "", message: str = DEMO_DISABLED_MESSAGE) -> None:
        self.action = action
        super().__init__(message)


class DemoUsageCapReached(DemoModeError):
    """The demo's LLM usage cap (per session, hourly, or daily) is spent."""

    def __init__(self, scope: str = "") -> None:
        self.scope = scope
        super().__init__("llm", DEMO_USAGE_CAP_MESSAGE)


def demo_mode_enabled() -> bool:
    return os.getenv("LEADLENS_DEMO_MODE", "").strip().lower() in _TRUE


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

_writes_allowed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "leadlens_demo_writes_allowed", default=False
)


@contextlib.contextmanager
def allow_demo_writes():
    """Permit database writes inside the block. ONLY for operator-run
    scripts (scripts/seed_demo.py, scripts/reset_demo.py) and Alembic's
    env.py. Application code must never enter this context — a test
    (tests/test_phase10_demo_guards.py) fails if any app module does."""
    token = _writes_allowed.set(True)
    try:
        yield
    finally:
        _writes_allowed.reset(token)


def writes_allowed() -> bool:
    return _writes_allowed.get()


def assert_write_allowed(kind: str = "write") -> None:
    if demo_mode_enabled() and not _writes_allowed.get():
        raise DemoModeError(kind)


# ---------------------------------------------------------------------------
# External side effects, exports, uploads — never bypassable
# ---------------------------------------------------------------------------

def assert_external_action_allowed(kind: str = "external action") -> None:
    if demo_mode_enabled():
        raise DemoModeError(kind)


def assert_export_allowed(kind: str = "export") -> None:
    if demo_mode_enabled():
        raise DemoModeError(kind)


def assert_upload_allowed(kind: str = "upload") -> None:
    if demo_mode_enabled():
        raise DemoModeError(kind)


# ---------------------------------------------------------------------------
# Runtime files
# ---------------------------------------------------------------------------

# Synthetic, committed, READ-ONLY runtime files for demo mode. The repo also
# ships small runtime files under data/ (they belong to a real clinic's
# checkout); the demo must never read those, so demo mode points every such
# reader here instead. Nothing writes into this directory in demo mode.
DEMO_SEED_DIR = Path(__file__).resolve().parents[1] / "demo" / "seed"


def demo_seed_path(name: str) -> Path:
    return DEMO_SEED_DIR / name
