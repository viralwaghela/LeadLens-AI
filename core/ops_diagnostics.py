"""Temporary, SAFE operator diagnostics for this deployment — not demo-specific.

Shown only when the operator sets LEADLENS_OPS_DIAGNOSTICS (unset by default, so this
changes nothing for any deployment that doesn't explicitly turn it on). Prints names,
lengths and prefixes only — NEVER the API key value. Remove the variable to hide it;
it does nothing otherwise. Companion to core/ops_llm_selftest.py.
"""
from __future__ import annotations

import os


def diagnostics_enabled() -> bool:
    return str(os.getenv("LEADLENS_OPS_DIAGNOSTICS", "")).strip().lower() in {"1", "true", "yes"}


def _key_shape(key: str) -> str:
    """Never the value — just enough to tell an old key from a new one, and roughly
    how long it is, without revealing anything an attacker could use."""
    if not key:
        return "not set"
    if key.startswith("sk-proj-"):
        kind = "project-scoped (sk-proj-...)"
    elif key.startswith("sk-"):
        kind = "legacy/account-scoped (sk-...)"
    else:
        kind = "unrecognized prefix"
    return f"{kind}, length={len(key)}"


def collect() -> dict[str, str]:
    from core.ops_llm_selftest import configured_model

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    return {
        "OPENAI_API_KEY": _key_shape(api_key),
        "OPENAI_MODEL (configured, this is what generate_ai_response() actually calls)": configured_model(),
        "OPENAI_MODEL explicitly set": str("OPENAI_MODEL" in os.environ),
        "OPENAI_FAST_MODEL (specialists' override, if any)": os.getenv("OPENAI_FAST_MODEL", "").strip() or "not set",
    }


def render_ops_diagnostics() -> None:
    if not diagnostics_enabled():
        return
    import streamlit as st

    with st.expander("Ops diagnostics (temporary — remove LEADLENS_OPS_DIAGNOSTICS)", expanded=True):
        for name, value in collect().items():
            st.write(f"**{name}:** {value}")

        st.divider()
        st.caption(
            "This makes one real, billed call to OpenAI using this deployment's configured "
            "key and model. Click only when you intend to spend it."
        )
        if st.button("Run one minimal live OpenAI test call", key="ops_llm_selftest_btn"):
            from core.ops_llm_selftest import run_selftest

            result = run_selftest()
            st.write(f"**authentication:** {result.authentication}")
            st.write(f"**billing/quota:** {result.billing}")
            st.write(f"**model access:** {result.model_access}")
            if result.error_category:
                st.write(f"**error category:** {result.error_category}")
            if result.http_status is not None:
                st.write(f"**HTTP status:** {result.http_status}")
            if result.error_type:
                st.write(f"**error.type:** {result.error_type}")
            if result.error_code:
                st.write(f"**error.code:** {result.error_code}")
            if result.error_message:
                st.write(f"**error.message:** {result.error_message}")
            st.write(result.detail)
