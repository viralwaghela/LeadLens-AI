"""Phase 9 — shared, minimal production observability primitives.

Deliberately small: standard library `logging` plus a handful of
conventions (error categories, a correlation/run id, a safe structured
log line), not a new logging framework. Every long-running or
business-relevant workflow (scheduler runs, integration actions,
backfill/repair/verify scripts) should log through `log_event()` so
operators can grep/trace by `run_id` and `category` without needing a
new tool.

Never logs secrets: `safe_context()` strips anything whose key looks
credential-shaped before it ever reaches a log line, as a defense-in-depth
backstop — callers should still never pass a secret value in the first
place.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

_SECRET_KEY_PATTERN = re.compile(
    r"(password|secret|token|api_key|apikey|credential|access_token|private_key)", re.IGNORECASE,
)


class ErrorCategory:
    """Consistent categories for major failure domains — used as the
    `category` field in log_event() and, where useful, as a tag on
    operator-facing error messages. Not an enum (deliberately: a plain
    string constant is easy to grep for in logs and doesn't force every
    caller to import an enum type for one field)."""

    DATABASE = "DATABASE"
    TENANT_RESOLUTION = "TENANT_RESOLUTION"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    INTEGRATION = "INTEGRATION"
    SCHEDULER = "SCHEDULER"
    CRM_SYNC = "CRM_SYNC"
    JARVIS = "JARVIS"
    CONFIGURATION = "CONFIGURATION"


def new_run_id() -> str:
    """A short, greppable correlation id for one execution of a
    long-running workflow (a scheduler pass, a backfill run, one
    integration action) — not a security token, just a trace handle."""
    return uuid.uuid4().hex[:12]


def safe_context(**fields: Any) -> dict[str, Any]:
    """Strips anything whose key looks credential-shaped before it's
    returned — a defense-in-depth backstop for log_event() callers, not
    a substitute for never passing a secret in the first place."""
    return {
        key: ("<redacted>" if _SECRET_KEY_PATTERN.search(key) else value)
        for key, value in fields.items()
    }


def log_event(
    logger: logging.Logger,
    *,
    level: int = logging.INFO,
    category: str,
    operation: str,
    run_id: str | None = None,
    organization_id: int | None = None,
    actor_type: str | None = None,
    detail: str = "",
    **extra: Any,
) -> None:
    """One structured log line: category, operation, run_id,
    organization_id, actor_type, and a short safe detail string, plus
    any additional safe fields. `organization_id` is safe to log (it's
    an integer id, never patient/business data) and is the single most
    useful field for tracing a multi-org issue back to one tenant.
    Extra fields are passed through safe_context() before formatting."""
    context = safe_context(**extra)
    parts = [f"category={category}", f"operation={operation}"]
    if run_id:
        parts.append(f"run_id={run_id}")
    if organization_id is not None:
        parts.append(f"organization_id={organization_id}")
    if actor_type:
        parts.append(f"actor_type={actor_type}")
    for key, value in context.items():
        parts.append(f"{key}={value}")
    if detail:
        parts.append(f"detail={detail}")
    logger.log(level, " ".join(parts))
