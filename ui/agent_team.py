from __future__ import annotations
import streamlit as st

AGENTS = [
    ("Chief of Staff", "Executive coordination", "Routes requests, combines specialist analysis, and recommends next actions."),
    ("Sales Agent", "Revenue & pipeline", "Analyses revenue signals, sales priorities, corporate offers, and conversion gaps."),
    ("Marketing Agent", "Growth & campaigns", "Reviews channel mix, campaign priorities, budget use, and lead-generation measurement."),
    ("Finance Agent", "Profit & budgets", "Monitors revenue, expenses, margin, acquisition cost, and budget decisions."),
    ("Operations Agent", "Delivery & capacity", "Tracks execution, service capacity, patient flow, cancellations, and operating bottlenecks."),
    ("HR Agent", "People & hiring", "Reviews workload, role clarity, hiring needs, and performance scorecards."),
    ("Analytics Agent", "Business intelligence", "Turns stored business records into cross-department trends and management signals."),
    ("Customer Success Agent", "Retention & experience", "Focuses on patient follow-up, satisfaction, reviews, referrals, and retention."),
]


def show_agent_team() -> None:
    st.markdown('<div class="eyebrow">YOUR AI WORKFORCE</div>', unsafe_allow_html=True)
    st.markdown("## Agent team")
    st.caption("A coordinated team of specialist agents working from the same Beyond Pain business memory.")
    cols = st.columns(2)
    for index, (name, domain, description) in enumerate(AGENTS):
        with cols[index % 2]:
            with st.container(border=True):
                left, right = st.columns([4, 1])
                left.markdown(f"### {name}")
                right.success("Active")
                st.caption(domain)
                st.write(description)
