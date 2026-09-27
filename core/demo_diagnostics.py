"""Temporary, SAFE startup diagnostics for the public demo deployment.

Shown only when the operator sets LEADLENS_DEMO_DIAGNOSTICS (in the environment, as a
top-level Streamlit secret, or inside any secrets section - so it still works if the
secrets were mis-nested). Prints names and true/false only - NEVER a secret value, and
never the database host. Remove the variable to hide it; it does nothing otherwise.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.demo_mode import _TRUE, _top_level_secrets, demo_mode_enabled

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _git_head() -> tuple[str, str]:
    branch, commit = _git("rev-parse", "--abbrev-ref", "HEAD"), _git("rev-parse", "--short", "HEAD")
    if commit:
        return branch or "?", commit
    try:  # no git binary: read the repository files directly
        head = (ROOT / ".git" / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1]
            return ref.rsplit("/", 1)[-1], (ROOT / ".git" / ref).read_text().strip()[:7]
        return "detached", head[:7]
    except Exception:  # noqa: BLE001
        return "undetectable", "undetectable"


def _secret_sections_containing(name: str) -> list[str]:
    try:
        import streamlit as st

        return [str(k) for k, v in dict(st.secrets).items() if isinstance(v, dict) and name in v]
    except Exception:  # noqa: BLE001
        return []


def diagnostics_enabled() -> bool:
    if str(os.getenv("LEADLENS_DEMO_DIAGNOSTICS", "")).strip().lower() in _TRUE:
        return True
    if str(_top_level_secrets().get("LEADLENS_DEMO_DIAGNOSTICS", "")).strip().lower() in _TRUE:
        return True
    return bool(_secret_sections_containing("LEADLENS_DEMO_DIAGNOSTICS"))


def collect() -> dict[str, str]:
    from core.auth import v2_auth_enabled

    branch, commit = _git_head()
    top = _top_level_secrets()
    nested = _secret_sections_containing("LEADLENS_DEMO_MODE")
    demo = demo_mode_enabled()
    return {
        "git branch": branch,
        "git commit": commit,
        "demo mode evaluates": str(demo),
        "LEADLENS_DEMO_MODE in environment": str("LEADLENS_DEMO_MODE" in os.environ),
        "LEADLENS_DEMO_MODE top-level secret": str("LEADLENS_DEMO_MODE" in top),
        "LEADLENS_DEMO_MODE nested in a secrets section": ", ".join(nested) or "no",
        "V2 auth enabled": str(v2_auth_enabled()),
        "auth path selected": "demo passwordless entry" if demo else ("V2 email/password" if v2_auth_enabled() else "LEGACY shared-password form"),
    }


def render_demo_diagnostics() -> None:
    if not diagnostics_enabled():
        return
    import streamlit as st

    with st.expander("Demo diagnostics (temporary — remove LEADLENS_DEMO_DIAGNOSTICS)", expanded=True):
        for name, value in collect().items():
            st.write(f"**{name}:** {value}")

        st.divider()
        st.caption(
            "This makes one real, billed call to OpenAI using this deployment's configured "
            "key. Click only when you intend to spend it."
        )
        if st.button("Run one minimal live OpenAI test call", key="demo_llm_selftest_btn"):
            from core.demo_llm_selftest import run_selftest

            result = run_selftest()
            st.write(f"**authentication:** {result.authentication}")
            st.write(f"**billing/quota:** {result.billing}")
            st.write(f"**model access:** {result.model_access}")
            if result.error_category:
                st.write(f"**error category:** {result.error_category}")
            st.write(result.detail)
