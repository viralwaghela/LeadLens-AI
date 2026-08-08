"""Standalone visual harness for the Core switch (docs/CORE_SWITCH_SPEC.md).

Renders just the sidebar switch — apply_workspace_theme() +
render_workspace_switch() — without the rest of the app, so the toggle,
its dormant/ignited states, and the forced-theme reload can be checked in
isolation. Not part of tests/: this is for eyeballing in a browser, not
automated assertion, and it deliberately mirrors dashboard.py's real
workspace_mode/query_params init so the reload behaves the same way it
does in the full app.

Run with: streamlit run test_core_switch.py
"""
from __future__ import annotations

import streamlit as st

from ui.workspace_theme import apply_workspace_theme, render_workspace_switch

st.set_page_config(page_title="Core switch harness", layout="wide")

if "workspace_mode" not in st.session_state:
    queried = st.query_params.get("workspace", "").upper()
    st.session_state["workspace_mode"] = queried if queried in ("CRM", "JARVIS") else "JARVIS"
mode = st.session_state["workspace_mode"]
apply_workspace_theme(mode)

with st.sidebar:
    render_workspace_switch()

st.title(f"Current workspace: {mode}")
st.write("Click the Core in the sidebar to toggle. Each toggle forces a theme reload.")
st.dataframe({"col": [1, 2, 3]})
