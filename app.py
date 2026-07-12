import streamlit as st

from core.memory import company_exists
from dashboard import show_dashboard
from onboarding import show_onboarding

st.set_page_config(
    page_title="LeadLens AI",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root { --ink:#1e1a24; --muted:#756f7d; --accent:#7255d9; --soft:#f5f1ff; --line:#e9e4ef; }
    .stApp { background:linear-gradient(180deg,#fcfbfd 0%,#f8f6fb 100%); color:var(--ink); }
    .block-container { max-width:1320px; padding-top:2rem; padding-bottom:4rem; }
    [data-testid="stSidebar"] { background:#f4f0fa; border-right:1px solid #e6e0ed; }
    [data-testid="stSidebar"] button { border-radius:12px; }
    h1,h2,h3 { letter-spacing:-0.025em; }
    .hero-shell { display:flex; justify-content:space-between; align-items:center; gap:2rem; padding:1.5rem 1.6rem; background:#fff; border:1px solid var(--line); border-radius:24px; box-shadow:0 14px 45px rgba(52,38,73,.06); margin-bottom:1.2rem; }
    .hero-shell h1 { margin:.2rem 0 .4rem; font-size:2.6rem; }
    .hero-shell p { margin:0; color:var(--muted); }
    .eyebrow { color:var(--accent); font-size:.72rem; font-weight:800; letter-spacing:.12em; }
    .health-score { min-width:145px; text-align:center; padding:1rem; border-radius:20px; background:var(--soft); }
    .health-score span { display:block; font-size:2.3rem; font-weight:800; color:var(--accent); }
    .health-score small { color:var(--muted); }
    .brief-row { display:grid; grid-template-columns:120px 1fr; gap:1rem; padding:1rem 0; border-bottom:1px solid var(--line); }
    .brief-row span { color:#4d4654; }
    .answer-card { margin-top:.7rem; padding:1.2rem 1.3rem; border:1px solid var(--line); border-radius:18px; background:#fff; line-height:1.7; }
    [data-testid="stMetric"] { background:#fff; border:1px solid var(--line); padding:1rem; border-radius:18px; }
    .stButton>button, .stDownloadButton>button, [data-testid="stFormSubmitButton"]>button { border-radius:12px; font-weight:700; }
    [data-testid="stFormSubmitButton"]>button[kind="primary"] { background:var(--accent); border-color:var(--accent); }
    div[data-testid="stVerticalBlockBorderWrapper"] { border-radius:18px; }
    </style>
    """,
    unsafe_allow_html=True,
)

if company_exists():
    show_dashboard()
else:
    show_onboarding()
