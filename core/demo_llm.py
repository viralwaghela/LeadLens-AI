"""Public-demo LLM budget: model pin, prompt/response size caps, and
per-session + global (hourly, daily) call limits.

Called from exactly one place — services/ai.py::generate_ai_response(),
the single choke point every LeadLens AI feature goes through — and only
when LEADLENS_DEMO_MODE is set.

Layers (each independent; the provider-side project budget you configure in
the OpenAI dashboard is the hard backstop behind all of them):

    model pin     the model is forced to DEMO_OPENAI_MODEL, ignoring any
                  per-call override (specialists ask for OPENAI_FAST_MODEL).
    size caps     prompts over DEMO_LLM_MAX_PROMPT_CHARS are refused; output
                  is capped at DEMO_LLM_MAX_OUTPUT_TOKENS.
    per session   DEMO_LLM_MAX_CALLS_PER_SESSION calls per browser session.
                  Easy to reset by opening a new session, so it only limits a
                  single visitor's burst — the global caps are the real limit.
    global        DEMO_LLM_MAX_CALLS_PER_HOUR and DEMO_LLM_MAX_CALLS_PER_DAY,
                  counted in the demo_usage table so they span every visitor
                  and survive a restart.

One visitor question fans out into several LLM calls (each specialist plus
the synthesis), so the limits count CALLS, and the defaults are sized for
that. When a cap is spent, DemoUsageCapReached (a RuntimeError, which every
caller already handles by falling back to templated text) carries a message
that is safe to show a visitor. Any failure to check the global counter
(database down, etc.) DENIES the call — it fails closed, never open.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from core.demo_mode import DemoModeError, DemoUsageCapReached, demo_mode_enabled

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_MAX_OUTPUT_TOKENS = 1500
DEFAULT_MAX_PROMPT_CHARS = 40000
DEFAULT_CALLS_PER_SESSION = 20
DEFAULT_CALLS_PER_HOUR = 120
DEFAULT_CALLS_PER_DAY = 600

LLM_SCOPE = "llm_calls"
_SESSION_KEY = "_demo_llm_calls"

# Used when there is no Streamlit runtime (tests, scripts).
_FALLBACK_SESSION: dict[str, int] = {}

_ENGINE = None


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default  # a malformed value must not silently remove the cap


def demo_model() -> str:
    return os.getenv("DEMO_OPENAI_MODEL", "").strip() or DEFAULT_MODEL


def demo_max_output_tokens() -> int:
    return max(1, _int_env("DEMO_LLM_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))


def check_prompt_size(*texts: object) -> None:
    limit = _int_env("DEMO_LLM_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS)
    total = sum(len(str(t or "")) for t in texts)
    if total > limit:
        raise DemoModeError("llm", "That request is too long for the public demo. Please shorten it.")


def _session_store():
    try:
        import streamlit as st

        store = st.session_state
        store.get(_SESSION_KEY)  # raises/warns outside a runtime in some versions
        return store
    except Exception:  # noqa: BLE001 - no Streamlit runtime
        return _FALLBACK_SESSION


def reset_fallback_session_for_tests() -> None:
    _FALLBACK_SESSION.clear()


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from core.db.session import make_engine

        _ENGINE = make_engine()
    return _ENGINE


def _consume(engine, window_key: str, scope: str, cap: int) -> bool:
    """Atomically take one unit from `scope` in `window_key` if it is under
    `cap`. Race-safe without relying on SAVEPOINTs: a guarded UPDATE (only
    increments while count < cap), then a first-row INSERT, retried once if
    a concurrent insert wins the unique constraint."""
    from core.db.models.demo import DemoUsageCounter
    from core.db.session import session_scope

    if cap <= 0:
        return False
    for _attempt in range(2):
        with session_scope(engine) as session:
            updated = session.execute(
                update(DemoUsageCounter)
                .where(
                    DemoUsageCounter.window_key == window_key,
                    DemoUsageCounter.scope == scope,
                    DemoUsageCounter.count < cap,
                )
                .values(count=DemoUsageCounter.count + 1)
            ).rowcount
            if updated:
                return True
            exists = session.execute(
                select(DemoUsageCounter.id).where(
                    DemoUsageCounter.window_key == window_key, DemoUsageCounter.scope == scope
                )
            ).first()
            if exists:
                return False  # row exists and is at the cap
            try:
                session.add(DemoUsageCounter(window_key=window_key, scope=scope, count=1))
                session.flush()
                return True
            except IntegrityError:
                session.rollback()  # lost the insert race; loop and UPDATE instead
    return False


def reserve_llm_call(*, engine=None, now: datetime | None = None) -> None:
    """Spend one LLM call from every applicable budget, or raise
    DemoUsageCapReached. Never raises anything else: a counter failure is
    treated as 'cap reached' so the demo fails closed."""
    if not demo_mode_enabled():
        return

    store = _session_store()
    per_session = _int_env("DEMO_LLM_MAX_CALLS_PER_SESSION", DEFAULT_CALLS_PER_SESSION)
    used = int(store.get(_SESSION_KEY, 0) or 0)
    if used >= per_session:
        raise DemoUsageCapReached("session")

    now = now or datetime.now(timezone.utc)
    try:
        eng = engine if engine is not None else _engine()
        if not _consume(eng, now.strftime("%Y-%m-%d"), LLM_SCOPE,
                        _int_env("DEMO_LLM_MAX_CALLS_PER_DAY", DEFAULT_CALLS_PER_DAY)):
            raise DemoUsageCapReached("day")
        if not _consume(eng, now.strftime("%Y-%m-%dT%H"), LLM_SCOPE,
                        _int_env("DEMO_LLM_MAX_CALLS_PER_HOUR", DEFAULT_CALLS_PER_HOUR)):
            raise DemoUsageCapReached("hour")
    except DemoUsageCapReached:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed on any counter failure
        raise DemoUsageCapReached("unavailable") from exc

    try:
        store[_SESSION_KEY] = used + 1
    except Exception:  # noqa: BLE001 - never let bookkeeping break a granted call
        _FALLBACK_SESSION[_SESSION_KEY] = used + 1
