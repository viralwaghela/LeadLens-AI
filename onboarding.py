"""First-run experience: Jarvis introduces himself and sets up the clinic.

Runs only once per business — app.py falls back here whenever
core.memory.company_exists() is False, and stops routing here again the
moment save_company() succeeds. This screen deliberately does NOT share
CSS with dashboard.py's apply_workspace_theme(): that function also drives
the Core switch's forced-theme-reload machinery (see ui/workspace_theme.py),
which depends on a `workspace` mode that doesn't exist yet at this point in
the flow — calling it here would reload the page before onboarding could
even render. Instead this file carries its own self-contained stylesheet
that matches the same Jarvis dark palette by eye.
"""
from __future__ import annotations

import time

import streamlit as st

from core.memory import save_company
from ui.icons import icon

STEP_WELCOME = 0
STEP_MEET_OWNER = 1
STEP_BUILDING = 2
STEP_MEET_TEAM = 3
STEP_TOUR = 4

TEAM = [
    ("megaphone", "Marketing Agent", "I help bring new patients into your clinic."),
    ("rupee", "Finance Agent", "I monitor revenue, expenses and profitability."),
    ("gear", "Operations Agent", "I make sure your clinic runs smoothly."),
    ("users", "Customer Success Agent", "I help keep your patients engaged."),
]

TOUR_STOPS = [
    ("home", "Dashboard", "Your clinic's health at a glance."),
    ("heart-pulse", "Patients", "Every patient record, in one place."),
    ("clipboard", "Appointments", "What's scheduled, and what's next."),
    ("bar-chart", "Reports", "How the business is actually doing."),
    ("sparkle", "Jarvis", "Ask me anything, anytime."),
]


def _css() -> None:
    st.markdown(
        """
        <style>
        .stApp{
            background:radial-gradient(circle at 82% 3%,rgba(0,115,255,.18),transparent 28%),
                radial-gradient(circle at 10% 95%,rgba(0,206,255,.10),transparent 32%),
                linear-gradient(145deg,#010611 0%,#020b18 48%,#05152a 100%);
        }
        .stApp [data-testid="stHeader"]{background:rgba(1,6,17,.78)}
        .stApp h1,.stApp h2,.stApp h3,.stApp p,.stApp label,.stApp span{color:#eaf7ff}
        .block-container{max-width:760px;padding-top:3rem}

        .ll-onboard-orb{width:76px;height:76px;border-radius:50%;margin:0 auto 1.1rem;
            display:grid;place-items:center;font-size:1.9rem;color:#e5ffff;
            background:radial-gradient(circle,#efffff 0 5%,#36dcff 7% 13%,#087bf2 16% 34%,#04172f 37% 52%,#0aa8ee 55% 58%,#031020 62%);
            border:1px solid #49dfff;box-shadow:0 0 18px #058eff,0 0 48px rgba(13,164,255,.55)}
        .ll-onboard-center{text-align:center;padding:1.5rem 0 .5rem}
        .ll-onboard-center h1{font-size:2rem;margin:0 0 .5rem}
        .ll-onboard-center p{color:#9fc5e1;font-size:1.02rem;max-width:520px;margin:0 auto .4rem;line-height:1.55}
        .ll-onboard-sub{color:#5fe3ff!important;font-size:.78rem;font-weight:800;
            letter-spacing:.14em;text-transform:uppercase;margin-top:1.1rem!important}

        .stApp [data-testid="stTextInput"] input,
        .stApp [data-testid="stTextArea"] textarea,
        .stApp [data-testid="stNumberInput"] input{
            background:#06172b!important;color:#eaf7ff!important;
            border-color:rgba(52,173,255,.30)!important}
        .stApp [data-testid="stFormSubmitButton"] button,
        .stApp [data-testid="stBaseButton-secondary"],
        .stApp .stButton>button{
            color:#e3f8ff;background:linear-gradient(135deg,#0984ff,#35d9ff)!important;
            border:none!important;font-weight:750}
        .stApp [data-testid="stProgress"] > div > div{background:rgba(30,144,255,.30)}
        .stApp [data-testid="stProgress"] > div > div > div{background:linear-gradient(90deg,#0984ff,#35d9ff)}

        .ll-agent-card{border:1px solid rgba(31,145,255,.28);
            background:linear-gradient(145deg,#06162a,#08213a);border-radius:14px;
            padding:1.1rem .9rem;text-align:center;min-height:150px}
        .ll-agent-icon{width:46px;height:46px;border-radius:50%;margin:0 auto .6rem;
            display:grid;place-items:center;background:radial-gradient(circle,#0c2138,#081a2d);
            border:1px solid rgba(52,173,255,.4);color:#4edfff}
        .ll-agent-name{font-size:.86rem;font-weight:800;color:#eaf7ff;margin-bottom:.3rem}
        .ll-agent-line{font-size:.76rem;color:#8fb4d2;line-height:1.4}

        .ll-tour-row{display:flex;align-items:center;gap:.85rem;padding:.7rem 0;
            border-bottom:1px solid rgba(31,145,255,.14)}
        .ll-tour-row:last-child{border-bottom:none}
        .ll-tour-icon{width:38px;height:38px;border-radius:11px;flex:none;
            display:grid;place-items:center;background:radial-gradient(circle,#0c2138,#081a2d);
            border:1px solid rgba(52,173,255,.35);color:#4edfff}
        .ll-tour-name{font-size:.86rem;font-weight:800;color:#eaf7ff}
        .ll-tour-line{font-size:.76rem;color:#8fb4d2}

        .ll-building-line{font-size:1.15rem;color:#cce9ff;text-align:center;
            padding:2rem 0;font-weight:650}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _progress(value: float) -> None:
    st.progress(value)


def _step_welcome() -> None:
    st.markdown(
        f'''<div class="ll-onboard-center">
            <div class="ll-onboard-orb">✦</div>
            <h1>Hello, I'm Jarvis.</h1>
            <p>Welcome to LeadLens CareOS.</p>
            <p>Think of me as your AI Chief of Staff. I'll help you run your clinic,
            coordinate your AI team, and keep you informed — so you can focus on
            treating patients.</p>
            <p class="ll-onboard-sub">Before we begin, I'd like to learn a little about your clinic.</p>
        </div>''',
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        if st.button("Let's set up your clinic", use_container_width=True, type="primary"):
            st.session_state["onboarding_step"] = STEP_MEET_OWNER
            st.rerun()


def _step_meet_owner() -> None:
    _progress(0.2)
    st.markdown(
        '''<div class="ll-onboard-center">
            <h1 style="font-size:1.5rem">Perfect. Let's get to know each other.</h1>
            <p>A few quick questions — nothing here is graded.</p>
        </div>''',
        unsafe_allow_html=True,
    )
    with st.form("meet_owner_form"):
        owner_name = st.text_input("What should I call you?")
        business_name = st.text_input("What's the name of your clinic?")
        employees = st.number_input(
            "How many therapists work here?", min_value=0, step=1, value=0
        )
        services = st.text_area("Which services do you provide?")
        clinic_timings = st.text_input("What are your clinic timings?")
        submitted = st.form_submit_button(
            "Continue", use_container_width=True, type="primary"
        )
    if submitted:
        if not owner_name.strip() or not business_name.strip():
            st.error("I'll need your name and your clinic's name to get started.")
            return
        st.session_state["onboarding_answers"] = {
            "owner_name": owner_name.strip(),
            "business_name": business_name.strip(),
            "employees": int(employees),
            "services": services.strip(),
            "clinic_timings": clinic_timings.strip(),
        }
        st.session_state["onboarding_step"] = STEP_BUILDING
        st.rerun()


def _step_building() -> None:
    # Advance immediately so nothing outside this function can replay the
    # animation on a later, unrelated rerun — this step has no widgets of
    # its own, so the only reruns that reach it are the one right after
    # _step_meet_owner submits, and the st.rerun() at the end of this call.
    st.session_state["onboarding_step"] = STEP_MEET_TEAM
    _progress(0.45)
    placeholder = st.empty()
    for message in ("Creating CRM...", "Configuring AI Team...", "Preparing Dashboard..."):
        placeholder.markdown(
            f'<div class="ll-building-line">{message}</div>', unsafe_allow_html=True
        )
        time.sleep(0.6)
    st.rerun()


def _step_meet_team() -> None:
    _progress(0.7)
    st.markdown(
        '''<div class="ll-onboard-center">
            <h1 style="font-size:1.5rem">Meet your AI team.</h1>
        </div>''',
        unsafe_allow_html=True,
    )
    cols = st.columns(4)
    for col, (icon_name, name, line) in zip(cols, TEAM):
        with col:
            st.markdown(
                f'''<div class="ll-agent-card">
                    <div class="ll-agent-icon">{icon(icon_name, 20)}</div>
                    <div class="ll-agent-name">{name}</div>
                    <div class="ll-agent-line">{line}</div>
                </div>''',
                unsafe_allow_html=True,
            )
    st.markdown(
        '<p style="text-align:center;margin-top:1.3rem;color:#cce9ff">'
        "Together we'll manage your clinic.</p>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        if st.button("Continue", use_container_width=True, type="primary"):
            st.session_state["onboarding_step"] = STEP_TOUR
            st.rerun()


def _step_tour() -> None:
    _progress(0.9)
    st.markdown(
        '''<div class="ll-onboard-center">
            <h1 style="font-size:1.5rem">Let me show you the five places you'll use most.</h1>
        </div>''',
        unsafe_allow_html=True,
    )
    for icon_name, name, line in TOUR_STOPS:
        st.markdown(
            f'''<div class="ll-tour-row">
                <div class="ll-tour-icon">{icon(icon_name, 18)}</div>
                <div><div class="ll-tour-name">{name}</div>
                <div class="ll-tour-line">{line}</div></div>
            </div>''',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<p style="text-align:center;margin-top:1.3rem;color:#8fb4d2">'
        "30 seconds. Done.</p>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        if st.button("Show me my dashboard", use_container_width=True, type="primary"):
            answers = st.session_state.get("onboarding_answers", {})
            save_company(
                {
                    "owner_name": answers.get("owner_name", ""),
                    "business_name": answers.get("business_name", ""),
                    "industry": "Healthcare",
                    "employees": answers.get("employees", 0),
                    "services": answers.get("services", ""),
                    "clinic_timings": answers.get("clinic_timings", ""),
                }
            )
            st.session_state.pop("onboarding_answers", None)
            st.session_state.pop("onboarding_step", None)
            st.session_state["show_onboarding_complete_toast"] = True
            st.rerun()


def show_onboarding() -> None:
    _css()
    step = st.session_state.get("onboarding_step", STEP_WELCOME)
    if step == STEP_MEET_OWNER:
        _step_meet_owner()
    elif step == STEP_BUILDING:
        _step_building()
    elif step == STEP_MEET_TEAM:
        _step_meet_team()
    elif step == STEP_TOUR:
        _step_tour()
    else:
        _step_welcome()
