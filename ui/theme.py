"""Shared visual system and lightweight UI helpers for LeadLens CareOS."""
from __future__ import annotations

from html import escape

import streamlit as st


THEME_CSS = """
<style>
:root {
    --ll-ink: #191724;
    --ll-ink-soft: #393447;
    --ll-muted: #756f82;
    --ll-accent: #6554e8;
    --ll-accent-strong: #5140d7;
    --ll-accent-soft: #eeebff;
    --ll-coral: #ff5b62;
    --ll-success: #16835a;
    --ll-success-soft: #e8f7f0;
    --ll-warning: #a75d08;
    --ll-warning-soft: #fff5df;
    --ll-danger: #b83b45;
    --ll-danger-soft: #fff0f1;
    --ll-surface: #ffffff;
    --ll-canvas: #f7f7fb;
    --ll-line: #e7e5ee;
    --ll-line-strong: #d9d5e3;
    --ll-shadow: 0 18px 55px rgba(36, 28, 58, .07);
}

html, body, [class*="css"] {
    font-family: Inter, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

.stApp {
    background:
        radial-gradient(circle at 88% 4%, rgba(101, 84, 232, .06), transparent 24rem),
        var(--ll-canvas);
    color: var(--ll-ink);
}

.block-container {
    max-width: 1480px;
    padding: 1.35rem 2rem 5rem;
}

h1, h2, h3 {
    color: var(--ll-ink);
    letter-spacing: -.035em;
}

h1 { font-size: clamp(2rem, 3vw, 3rem); }
h2 { font-size: clamp(1.55rem, 2vw, 2.15rem); }
h3 { font-size: 1.25rem; }

p, li { color: var(--ll-ink-soft); }

[data-testid="stHeader"] {
    background: rgba(247, 247, 251, .88);
    backdrop-filter: blur(14px);
}

[data-testid="stSidebar"] {
    background: #f1eff8;
    border-right: 1px solid var(--ll-line);
}

[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.25rem;
}

[data-testid="stSidebar"] .stButton > button {
    min-height: 2.75rem;
}

.ll-brand {
    padding: .3rem .15rem 1.05rem;
}

.ll-brand-mark {
    width: 2.35rem;
    height: 2.35rem;
    display: inline-grid;
    place-items: center;
    margin-bottom: .7rem;
    border-radius: 13px;
    color: white;
    background: linear-gradient(135deg, var(--ll-accent), #8e65ed);
    box-shadow: 0 9px 24px rgba(101, 84, 232, .25);
    font-size: 1.1rem;
}

.ll-brand-name {
    color: var(--ll-ink);
    font-size: 1.22rem;
    font-weight: 800;
    letter-spacing: -.025em;
}

.ll-brand-subtitle {
    color: var(--ll-muted);
    font-size: .82rem;
    line-height: 1.45;
    margin-top: .22rem;
}

.ll-sidebar-label {
    color: var(--ll-muted);
    font-size: .67rem;
    font-weight: 800;
    letter-spacing: .11em;
    text-transform: uppercase;
    margin: .65rem 0 .35rem;
}

.ll-sidebar-footer {
    padding: .4rem .15rem;
}

.ll-sidebar-footer strong {
    display: block;
    color: var(--ll-ink);
    font-size: .91rem;
}

.ll-sidebar-footer span {
    color: var(--ll-muted);
    font-size: .78rem;
}

.ll-live-dot {
    width: .48rem;
    height: .48rem;
    display: inline-block;
    margin-right: .4rem;
    border-radius: 50%;
    background: #1ea672;
    box-shadow: 0 0 0 4px rgba(30, 166, 114, .10);
}

.ll-workspace-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    min-height: 2.7rem;
    margin-bottom: 1.1rem;
    padding: .55rem .8rem .55rem 1rem;
    background: rgba(255, 255, 255, .78);
    border: 1px solid var(--ll-line);
    border-radius: 14px;
    box-shadow: 0 8px 28px rgba(36, 28, 58, .035);
}

.ll-workspace-path {
    color: var(--ll-muted);
    font-size: .78rem;
    font-weight: 650;
}

.ll-workspace-path strong {
    color: var(--ll-ink);
}

.ll-system-status {
    display: inline-flex;
    align-items: center;
    color: var(--ll-success);
    background: var(--ll-success-soft);
    padding: .35rem .58rem;
    border-radius: 999px;
    font-size: .71rem;
    font-weight: 750;
    white-space: nowrap;
}

.ll-page-header {
    margin: .35rem 0 1.35rem;
}

.ll-page-eyebrow, .eyebrow {
    color: var(--ll-accent);
    font-size: .69rem;
    font-weight: 850;
    letter-spacing: .12em;
    line-height: 1.45;
    text-transform: uppercase;
}

.ll-page-header h1 {
    margin: .28rem 0 .42rem;
    font-size: clamp(2rem, 3vw, 2.8rem);
}

.ll-page-header p {
    max-width: 760px;
    margin: 0;
    color: var(--ll-muted);
    line-height: 1.6;
}

.hero-shell, .welcome-hero, .jarvis-hero {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 2rem;
    padding: 1.65rem 1.75rem;
    margin-bottom: 1.25rem;
    overflow: hidden;
    background: linear-gradient(135deg, #fff 0%, #f5f2ff 100%);
    border: 1px solid var(--ll-line);
    border-radius: 24px;
    box-shadow: var(--ll-shadow);
}

.hero-shell h1, .welcome-hero h1, .jarvis-hero h1 {
    max-width: 900px;
    margin: .2rem 0 .48rem;
    font-size: clamp(2rem, 3vw, 2.85rem);
}

.hero-shell p, .welcome-hero p, .jarvis-hero p {
    max-width: 800px;
    margin: 0;
    color: var(--ll-muted);
    line-height: 1.55;
}

.health-score, .welcome-badge {
    min-width: 145px;
    padding: 1rem;
    text-align: center;
    background: var(--ll-accent-soft);
    border: 1px solid rgba(101, 84, 232, .10);
    border-radius: 20px;
}

.health-score span, .welcome-badge span {
    display: block;
    color: var(--ll-accent);
    font-size: 2.35rem;
    font-weight: 850;
}

.health-score small, .welcome-badge small {
    color: var(--ll-muted);
}

.brief-row {
    display: grid;
    grid-template-columns: 120px 1fr;
    gap: 1rem;
    padding: 1rem 0;
    border-bottom: 1px solid var(--ll-line);
}

.answer-card {
    margin-top: .7rem;
    padding: 1.25rem 1.35rem;
    background: var(--ll-surface);
    border: 1px solid var(--ll-line);
    border-radius: 18px;
    line-height: 1.7;
}

.jarvis-alert-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 1rem 1.2rem;
    margin: .4rem 0 .8rem;
    background: linear-gradient(135deg, #fff8ed, #fff2f3);
    border: 1px solid #fed7aa;
    border-radius: 18px;
}

.jarvis-alert-kicker {
    color: #c2410c;
    font-size: .68rem;
    font-weight: 850;
    letter-spacing: .12em;
}

.jarvis-alert-title {
    margin: .15rem 0;
    color: var(--ll-ink);
    font-size: 1.05rem;
    font-weight: 800;
}

.jarvis-alert-copy { color: #6b5d56; font-size: .9rem; }

.jarvis-hero {
    color: #f7f8ff;
    background:
        radial-gradient(circle at 85% 10%, rgba(73, 224, 255, .20), transparent 28%),
        radial-gradient(circle at 15% 90%, rgba(162, 92, 255, .22), transparent 32%),
        linear-gradient(135deg, #090b19, #15152d 55%, #101b31);
    border: 1px solid rgba(135, 170, 255, .25);
    box-shadow: 0 22px 70px rgba(4, 8, 24, .30);
}

.jarvis-hero h1 { color: #fff; }
.jarvis-hero p { color: #c9cee9; }

.jarvis-status {
    color: #6ee7ff;
    font-size: .69rem;
    font-weight: 850;
    letter-spacing: .14em;
}

.jarvis-orb {
    width: 72px;
    height: 72px;
    min-width: 72px;
    display: grid;
    place-items: center;
    color: #fff;
    background: radial-gradient(circle at 35% 35%, #7df9ff, #6f4ee8 55%, #15152d 72%);
    border-radius: 50%;
    box-shadow: 0 0 38px rgba(101, 218, 255, .42);
    font-size: 2rem;
}

[data-testid="stMetric"] {
    min-height: 124px;
    padding: 1rem 1.05rem;
    background: rgba(255, 255, 255, .92);
    border: 1px solid var(--ll-line);
    border-radius: 18px;
    box-shadow: 0 8px 26px rgba(36, 28, 58, .035);
}

[data-testid="stMetricLabel"] { color: var(--ll-muted); }
[data-testid="stMetricValue"] { color: var(--ll-ink); letter-spacing: -.035em; }

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(255, 255, 255, .78);
    border-color: var(--ll-line);
    border-radius: 18px;
    box-shadow: 0 8px 26px rgba(36, 28, 58, .025);
}

[data-testid="stForm"] {
    padding: 1.1rem;
    background: rgba(255, 255, 255, .75);
    border: 1px solid var(--ll-line);
    border-radius: 18px;
}

[data-baseweb="input"] > div,
[data-baseweb="select"] > div,
[data-baseweb="textarea"] > div {
    background: #fff;
    border-color: var(--ll-line-strong);
    border-radius: 11px;
}

[data-baseweb="input"] > div:focus-within,
[data-baseweb="select"] > div:focus-within,
[data-baseweb="textarea"] > div:focus-within {
    border-color: var(--ll-accent);
    box-shadow: 0 0 0 3px rgba(101, 84, 232, .11);
}

.stButton > button,
.stDownloadButton > button,
[data-testid="stFormSubmitButton"] > button {
    min-height: 2.6rem;
    color: var(--ll-ink-soft);
    background: #fff;
    border-color: var(--ll-line-strong);
    border-radius: 11px;
    font-weight: 720;
    transition: transform .14s ease, border-color .14s ease, box-shadow .14s ease;
}

.stButton > button:hover,
.stDownloadButton > button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
    color: var(--ll-accent-strong);
    border-color: var(--ll-accent);
    box-shadow: 0 7px 18px rgba(101, 84, 232, .10);
    transform: translateY(-1px);
}

.stButton > button[kind="primary"],
[data-testid="stFormSubmitButton"] > button[kind="primary"] {
    color: #fff;
    background: linear-gradient(135deg, var(--ll-accent), #7659e8);
    border-color: transparent;
    box-shadow: 0 10px 24px rgba(101, 84, 232, .20);
}

[data-baseweb="tab-list"] {
    gap: .3rem;
    padding: .3rem;
    overflow-x: auto;
    background: #efedf4;
    border-radius: 13px;
}

[data-baseweb="tab"] {
    min-width: max-content;
    padding: .62rem .88rem;
    border-radius: 9px;
}

[data-baseweb="tab"][aria-selected="true"] {
    background: #fff;
    box-shadow: 0 5px 14px rgba(36, 28, 58, .06);
}

[data-testid="stDataFrame"] {
    overflow: hidden;
    border: 1px solid var(--ll-line);
    border-radius: 14px;
}

[data-testid="stChatMessage"] {
    padding: .65rem .8rem;
    background: rgba(255, 255, 255, .86);
    border: 1px solid var(--ll-line);
    border-radius: 18px;
}

[data-testid="stAlert"] {
    border-radius: 14px;
}

[data-testid="stExpander"] {
    overflow: hidden;
    background: rgba(255, 255, 255, .65);
    border-color: var(--ll-line);
    border-radius: 13px;
}

hr { border-color: var(--ll-line); }

@media (max-width: 900px) {
    .block-container {
        padding: 1rem 1rem 4rem;
    }

    .hero-shell, .welcome-hero, .jarvis-hero {
        align-items: flex-start;
        flex-direction: column;
    }

    .health-score, .welcome-badge { width: 100%; }
    .ll-workspace-bar { align-items: flex-start; flex-direction: column; }
    .brief-row { grid-template-columns: 1fr; gap: .3rem; }
    [data-baseweb="tab-list"] { flex-wrap: nowrap; }
}
</style>
"""


def apply_theme() -> None:
    """Install the shared LeadLens visual system on the current page."""
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def render_workspace_bar(
    section: str,
    page: str,
    *,
    status: str = "AI monitoring",
) -> None:
    """Render a compact context bar above a workspace."""
    st.markdown(
        (
            '<div class="ll-workspace-bar">'
            f'<div class="ll-workspace-path">{escape(section)} / '
            f'<strong>{escape(page)}</strong></div>'
            f'<div class="ll-system-status"><span class="ll-live-dot"></span>'
            f'{escape(status)}</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_page_header(eyebrow: str, title: str, description: str) -> None:
    """Render a consistent page title and description."""
    st.markdown(
        (
            '<div class="ll-page-header">'
            f'<div class="ll-page-eyebrow">{escape(eyebrow)}</div>'
            f"<h1>{escape(title)}</h1>"
            f"<p>{escape(description)}</p>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
