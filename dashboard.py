from __future__ import annotations

import streamlit as st

from core.memory import load_company
from core.auth import render_logout_control
from ui.action_center import show_action_center
from ui.business_jarvis_suite import (
    show_agent_council,
    show_execution_center,
    show_meeting_mode,
)
from ui.clinic_jarvis import (
    show_autonomous_workflows,
    show_memory_center,
    show_patient_intelligence,
)
from ui.crm_dashboard import show_crm_dashboard
from ui.data_hub import show_data_hub
from ui.icons import icon
from ui.jarvis_mode import show_jarvis_mode
from ui.patient_crm import (
    show_appointments_page,
    show_crm_insights,
    show_patient_directory,
    show_payments_page,
    show_team_page,
    show_treatment_plans,
)
from ui.phases_16_to_20 import (
    show_live_workflows,
    show_outcome_learning,
    show_security_center,
)
from ui.phases_21_to_23 import (
    show_phase21_integrations,
    show_phase23_agent_collaboration,
)
from ui.workspace_theme import apply_workspace_theme, render_workspace_switch


def _brand(mode: str) -> None:
    title = "✦ LeadLens CareOS" if mode == "CRM" else "◉ JARVIS"
    subtitle = (
        "Patient records made simple"
        if mode == "CRM"
        else "AI command environment"
    )
    st.markdown(
        f"""
        <div class="sidebar-brand">
            <h2>{title}</h2>
            <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _clinic_card(company: dict) -> None:
    name = company.get("business_name", "LeadLens CareOS")
    industry = (company.get("industry") or "").lower()
    if industry == "healthcare" and "clinic" not in name.lower():
        name = f"{name} Clinic"
    location = company.get("location", "")
    st.markdown(
        f'''<div class="jv-sidebar-clinic">
            <div class="jv-sidebar-clinic-row">
                <div class="jv-sidebar-clinic-icon">{icon("clinic", 18)}</div>
                <div>
                    <div class="jv-sidebar-clinic-name">{name}</div>
                    <div class="jv-sidebar-clinic-loc">{location}</div>
                </div>
            </div>
        </div>''',
        unsafe_allow_html=True,
    )
    if st.button(
        "Switch Clinic",
        key="switch_clinic_btn",
        use_container_width=True,
        icon=":material/swap_horiz:",
    ):
        st.toast("Only one clinic workspace is configured right now.")


def _crm_settings() -> None:
    st.markdown(
        '<div class="eyebrow">CRM · SETTINGS</div>',
        unsafe_allow_html=True,
    )
    st.title("Clinic settings")
    st.caption("Maintain clinic details and review local data protection.")
    data, security = st.tabs(["Clinic details", "Data protection"])
    with data:
        show_data_hub()
    with security:
        show_security_center()


def _jarvis_team() -> None:
    team, council, meeting = st.tabs(
        ["AI team", "Specialist council", "Daily huddle"]
    )
    with team:
        show_phase23_agent_collaboration()
    with council:
        show_agent_council()
    with meeting:
        show_meeting_mode()


def _jarvis_workflows() -> None:
    prepared, live, learning = st.tabs(
        ["Prepare", "Live activity", "Outcome learning"]
    )
    with prepared:
        show_autonomous_workflows()
    with live:
        show_live_workflows()
    with learning:
        show_outcome_learning()


def _jarvis_approvals() -> None:
    pending, history = st.tabs(["Tasks & approvals", "Execution history"])
    with pending:
        show_action_center()
    with history:
        show_execution_center()


JARVIS_PRIMARY_PAGES = [
    "Mission Control",
    "Patient Intelligence",
    "AI Team",
    "Workflows",
    "Integrations",
    "Approvals",
]
JARVIS_SECONDARY_PAGES = [
    "Data Hub",
    "Reports",
    "Memory Center",
]


def show_dashboard() -> None:
    company = load_company()
    if st.session_state.pop("show_onboarding_complete_toast", False):
        st.toast("Clinic Configured — LeadLens is now managing your clinic.", icon="✅")
    if "workspace_mode" not in st.session_state:
        # A forced theme reload (see ui/workspace_theme._force_streamlit_theme)
        # does a real browser navigation, which starts a brand-new session —
        # st.session_state doesn't survive it, only the URL does. Recover the
        # mode from the query string so the reload doesn't silently bounce
        # the workspace back to the CRM default.
        queried = st.query_params.get("workspace", "").upper()
        st.session_state["workspace_mode"] = queried if queried in ("CRM", "JARVIS") else "CRM"
    mode = st.session_state["workspace_mode"]
    apply_workspace_theme(mode)

    with st.sidebar:
        _brand(mode)
        render_workspace_switch()
        st.divider()
        if mode == "CRM":
            pages = [
                "Patients",
                "Appointments",
                "Treatments & Plans",
                "Follow-ups",
                "Dashboard",
                "Payments",
                "Clinic Team",
                "Settings",
            ]
            page = st.radio(
                "Clinic records",
                pages,
                key="crm_page",
                label_visibility="collapsed",
            )
        else:
            def _clear_secondary():
                st.session_state["jarvis_secondary_page"] = None

            def _clear_primary():
                st.session_state["jarvis_page"] = None

            page = st.radio(
                "Jarvis systems",
                JARVIS_PRIMARY_PAGES,
                key="jarvis_page",
                label_visibility="collapsed",
                index=None if st.session_state.get("jarvis_secondary_page") else 0,
                on_change=_clear_secondary,
            )
            st.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)
            secondary = st.radio(
                "Jarvis systems (secondary)",
                JARVIS_SECONDARY_PAGES,
                key="jarvis_secondary_page",
                label_visibility="collapsed",
                index=None,
                on_change=_clear_primary,
            )
            page = secondary or page or "Mission Control"

        st.divider()
        _clinic_card(company)
        render_logout_control()

    if mode == "CRM":
        routes = {
            "Patients": show_patient_directory,
            "Appointments": show_appointments_page,
            "Treatments & Plans": show_treatment_plans,
            "Follow-ups": show_crm_insights,
            "Dashboard": show_crm_dashboard,
            "Payments": show_payments_page,
            "Clinic Team": show_team_page,
            "Settings": _crm_settings,
        }
    else:
        routes = {
            "Mission Control": show_jarvis_mode,
            "Patient Intelligence": show_patient_intelligence,
            "AI Team": _jarvis_team,
            "Workflows": _jarvis_workflows,
            "Integrations": show_phase21_integrations,
            "Approvals": _jarvis_approvals,
            "Data Hub": show_data_hub,
            "Reports": show_crm_dashboard,
            "Memory Center": show_memory_center,
        }
    routes[page]()
