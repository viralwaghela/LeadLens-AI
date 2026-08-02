
"""User interface for LeadLens Business Jarvis phases 11-15."""
from __future__ import annotations

import streamlit as st

from services.business_jarvis_engine import (
    agent_council,
    build_business_context,
    dynamic_chief_of_staff_answer,
    meeting_brief,
    prepare_execution,
    record_learning,
    revenue_plan,
    simulate_scenario,
)


def _money(value):
    return f"₹{float(value or 0):,.0f}"


def show_business_jarvis():
    st.markdown('<div class="eyebrow">EXECUTIVE ASSISTANT</div>', unsafe_allow_html=True)
    st.title("Business Jarvis")
    st.caption("Ask management questions using the clinic's stored business memory.")
    c = build_business_context()
    a,b,c3,d = st.columns(4)
    a.metric("Revenue", _money(c["revenue"]))
    b.metric("Profit", _money(c["profit"]))
    c3.metric("Open actions", c["open_tasks"])
    d.metric("Pending approvals", c["pending_approvals"])

    query = st.text_area(
        "Management question",
        "What should Beyond Pain focus on this month, and in what order?",
        height=100,
    )
    if st.button("Ask Business Jarvis", type="primary", use_container_width=True):
        st.markdown(dynamic_chief_of_staff_answer(query))


def show_scenario_simulator():
    st.markdown('<div class="eyebrow">DECISION SIMULATION</div>', unsafe_allow_html=True)
    st.title("Scenario Simulator")
    c = build_business_context()
    scenario = st.text_input("Scenario", "Hire one part-time therapist")
    col1,col2,col3 = st.columns(3)
    monthly_cost = col1.number_input("Additional monthly cost", min_value=0.0, value=35000.0, step=5000.0)
    capacity = col2.number_input("Expected capacity increase (%)", min_value=0.0, value=25.0, step=5.0)
    direct = col3.number_input("Expected direct monthly revenue", min_value=0.0, value=50000.0, step=5000.0)
    if st.button("Simulate decision", type="primary"):
        result = simulate_scenario(scenario, c["revenue"], monthly_cost, capacity, direct)
        x,y,z = st.columns(3)
        x.metric("Projected revenue", _money(result["projected_revenue"]))
        y.metric("Gross monthly gain", _money(result["monthly_gain"]))
        z.metric("Net monthly gain", _money(result["monthly_net_gain"]))
        st.info(result["recommendation"])
        if st.button("Create pilot task"):
            prepare_execution(
                f"30-day pilot: {scenario}",
                "Clinic owner",
                f"Approve a controlled pilot. Expected net monthly gain: {_money(result['monthly_net_gain'])}.",
            )
            st.success("Pilot task, approval and execution draft created.")


def show_revenue_coach():
    st.markdown('<div class="eyebrow">REVENUE PLANNING</div>', unsafe_allow_html=True)
    st.title("Revenue Coach")
    context = build_business_context()
    target = st.number_input(
        "Target monthly revenue",
        min_value=0.0,
        value=max(700000.0, float(context["revenue"])),
        step=50000.0,
    )
    plan = revenue_plan(target)
    a,b,c = st.columns(3)
    a.metric("Current", _money(plan["current"]))
    b.metric("Target", _money(plan["target"]))
    c.metric("Gap", _money(plan["gap"]))
    st.subheader("Suggested revenue path")
    for row in plan["channels"]:
        st.write(f"**{row['channel']}** — {_money(row['monthly_target'])}/month")
        st.progress(min(1.0, row["share"]))
    st.caption("Targets are planning estimates, not guaranteed outcomes.")


def show_agent_council():
    st.markdown('<div class="eyebrow">AI LEADERSHIP TEAM</div>', unsafe_allow_html=True)
    st.title("AI Executive Council")
    st.caption("Specialist agents review the same business memory before the Chief of Staff prioritises.")
    findings = agent_council()
    for item in findings:
        with st.container(border=True):
            st.subheader(item["agent"])
            st.write(f"**Finding:** {item['finding']}")
            st.write(f"**Recommendation:** {item['recommendation']}")
    st.subheader("Chief of Staff synthesis")
    st.markdown(dynamic_chief_of_staff_answer("Synthesize the executive council and recommend the priority order."))


def show_meeting_mode():
    st.markdown('<div class="eyebrow">DAILY MANAGEMENT MEETING</div>', unsafe_allow_html=True)
    st.title("Daily Management Meeting")
    brief = meeting_brief()
    st.caption(f"Generated {brief['generated_at']}")
    st.info(brief["headline"])
    st.subheader("Priority agenda")
    for idx, item in enumerate(brief["priorities"], 1):
        with st.container(border=True):
            st.write(f"**{idx}. {item['title']}**")
            st.write(item["why"])
            st.caption(f"Owner: {item['owner']} · Impact: {item['impact']}")
            st.write(f"Next action: {item['action']}")
    st.subheader("Owner questions")
    for q in brief["questions"]:
        st.write(f"- {q}")

    with st.expander("Teach Jarvis from an outcome"):
        observation = st.text_input("What action or pattern should Jarvis remember?")
        outcome = st.text_area("What happened as a result?")
        if st.button("Save learning"):
            if observation.strip() and outcome.strip():
                record_learning(observation, outcome)
                st.success("Learning saved to business memory.")
            else:
                st.warning("Enter both the observation and outcome.")


def show_execution_center():
    st.markdown('<div class="eyebrow">APPROVAL WORKFLOWS</div>', unsafe_allow_html=True)
    st.title("Execution Center")
    st.caption("LeadLens prepares actions. External sending remains disabled until credentials and approval are available.")
    workflow = st.selectbox(
        "Workflow",
        ["Inactive patient recovery", "Package renewal campaign", "Corporate wellness outreach", "Appointment reminder"],
    )
    recipient = st.text_input("Recipient or audience", "Selected clinic contacts")
    default_messages = {
        "Inactive patient recovery": "We noticed you have not visited recently. Would you like help scheduling your next session?",
        "Package renewal campaign": "Your current package is nearing completion. We can help you select the best continuation plan.",
        "Corporate wellness outreach": "Beyond Pain offers workplace posture, pain-prevention and employee wellness sessions.",
        "Appointment reminder": "This is a reminder for your upcoming appointment with Beyond Pain.",
    }
    message = st.text_area("Prepared message", default_messages[workflow], height=150)
    if st.button("Prepare for approval", type="primary"):
        prepare_execution(workflow, recipient, message)
        st.success("Draft created. A task and high-risk approval were added to Action Center.")
        st.info("Nothing has been sent externally.")
