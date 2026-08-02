import json
import streamlit as st

import agents
from agents.router import route_task

from core.memory import (
    load_memory,
    add_task,
    add_approval,
    add_daily_log,
    add_memory_entry,
)

from coo.planner import build_coo_briefing_prompt
from services.ai import generate_ai_response
from services.json_utils import parse_json_response
from coo.business_health import generate_business_snapshot


def parse_ai_json(response):
    return parse_json_response(response)


def show_list(title, items, fields):
    st.subheader(title)

    if not items:
        st.info("No items generated.")
        return

    for item in items:
        st.write(f"**{item.get('title', 'Untitled')}**")

        for field in fields:
            if field in item:
                st.write(f"**{field.replace('_', ' ').title()}:** {item.get(field)}")

        st.divider()


def save_coo_plan_to_memory(plan):
    routing_results = []

    for task in plan.get("todays_priorities", []):
        saved_task = add_task(
            task.get("title", ""),
            task.get("department", "Operations"),
            task.get("priority", "Medium")
        )

        routing_result = route_task(task)
        routing_result["saved_task_id"] = saved_task.get("id") if saved_task else ""
        routing_results.append(routing_result)

    for approval in plan.get("approval_requests", []):
        add_approval(
            approval.get("title", ""),
            approval.get("department", "Operations"),
            approval.get("risk_level", "Medium")
        )

    for risk in plan.get("risks", []):
        add_memory_entry("reports", {
            "type": "Risk",
            "title": risk.get("title", ""),
            "department": risk.get("department", ""),
            "severity": risk.get("severity", ""),
            "reason": risk.get("reason", "")
        })

    for opportunity in plan.get("opportunities", []):
        add_memory_entry("reports", {
            "type": "Opportunity",
            "title": opportunity.get("title", ""),
            "department": opportunity.get("department", ""),
            "potential_impact": opportunity.get("potential_impact", ""),
            "reason": opportunity.get("reason", "")
        })

    for result in routing_results:
        add_memory_entry("reports", {
            "type": "Agent Routing",
            "department": result.get("department", ""),
            "success": result.get("success", False),
            "message": result.get("message", ""),
            "saved_task_id": result.get("saved_task_id", "")
        })

    if plan.get("daily_log"):
        add_daily_log(plan.get("daily_log"))

    return True


def display_coo_plan(plan):
    st.metric(
        "Generated Business Health Score",
        f"{plan.get('business_health_score', 0)}/100"
    )

    st.subheader("Executive Summary")
    st.info(plan.get("executive_summary", ""))

    show_list(
        "Today's Priorities",
        plan.get("todays_priorities", []),
        ["department", "priority", "reason"]
    )

    show_list(
        "Risks",
        plan.get("risks", []),
        ["department", "severity", "reason"]
    )

    show_list(
        "Opportunities",
        plan.get("opportunities", []),
        ["department", "potential_impact", "reason"]
    )

    show_list(
        "Approval Requests",
        plan.get("approval_requests", []),
        ["department", "risk_level", "reason"]
    )


def show_coo_dashboard():
    st.subheader("🧠 AI COO")

    if "latest_coo_plan" not in st.session_state:
        st.session_state.latest_coo_plan = None

    snapshot = generate_business_snapshot()

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Business Health", f"{snapshot.get('health_score', 0)}/100")
    col2.metric("Business Stage", snapshot.get("business_stage", "Unknown"))
    col3.metric("Open Tasks", snapshot.get("open_tasks", 0))
    col4.metric("Pending Approvals", snapshot.get("pending_approvals", 0))

    st.divider()

    start_day = st.button("🚀 Start My Business Day", use_container_width=True)

    if start_day:
        memory = load_memory()
        snapshot = generate_business_snapshot()

        prompt = build_coo_briefing_prompt(memory, snapshot)

        with st.spinner("COO is reviewing your business..."):
            response = generate_ai_response(
                prompt,
                "You are an AI Chief Operating Officer."
            )

        plan = parse_ai_json(response)

        if plan:
            st.session_state.latest_coo_plan = plan
        else:
            st.error("COO returned invalid JSON.")
            st.text_area("Raw COO Output", response, height=400)

    if st.session_state.latest_coo_plan:
        display_coo_plan(st.session_state.latest_coo_plan)

        if st.button("Save COO Plan to Business Memory", use_container_width=True):
            save_coo_plan_to_memory(st.session_state.latest_coo_plan)
            st.success("COO plan saved and routed to available agents.")
            st.session_state.latest_coo_plan = None
            st.rerun()