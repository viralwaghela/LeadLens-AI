"""Public-demo UI helpers.

Presentation only — the enforcement lives server-side (core/demo_mode.py,
core/db/demo_guard.py, core/memory.py, the adapters). This module exists so
the UI degrades gracefully instead of crashing when a guard says no, and so
export bytes are never even *built* in demo mode.
"""
from __future__ import annotations

from typing import Callable

import streamlit as st

from core.demo_mode import DEMO_DISABLED_MESSAGE, DemoModeError, assert_export_allowed


def guarded_download_button(label: str, data_factory: Callable[[], bytes | str], *args, **kwargs):
    """Drop-in for st.download_button that takes a data FACTORY instead of
    the data. In demo mode the factory is never called — no export bytes are
    produced — and a disabled button explains why. Outside demo mode it is
    exactly st.download_button(label, data_factory(), ...)."""
    try:
        assert_export_allowed(label)
    except DemoModeError as error:
        # Positional file_name/mime are dropped; the button is inert anyway.
        kwargs = {k: v for k, v in kwargs.items() if k not in {"data", "disabled", "help"}}
        return st.download_button(label, b"", disabled=True, help=str(error), **kwargs)
    return st.download_button(label, data_factory(), *args, **kwargs)


def demo_disabled_notice() -> None:
    st.info(DEMO_DISABLED_MESSAGE)
