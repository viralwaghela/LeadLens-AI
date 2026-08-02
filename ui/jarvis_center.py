from __future__ import annotations

import json
import streamlit as st

from core.memory import load_memory
from services.briefing_engine import generate_morning_brief
from services.campaign_engine import create_campaign, update_campaign_status
from services.execution_engine import execute_action, propose_action
from services.integration_hub import list_integrations, save_connection
from services.memory_engine import remember
from services.monitoring_engine import run_monitors


def _money(v: float) -> str:
    return f"₹{v:,.0f}"


def show_jarvis_center() -> None:
    st.markdown('<div class="eyebrow">INTELLIGENCE · MEMORY · EXECUTION</div>', unsafe_allow_html=True)
    st.markdown("## Jarvis Control Center")
    st.caption("A controlled-autonomy workspace: LeadLens monitors the business, prepares actions, and executes only after approval.")
    tabs = st.tabs(["Morning Brief", "Business Memory", "Campaign Studio", "Monitoring", "Execution", "Integrations"])

    with tabs[0]:
        brief = generate_morning_brief()
        st.markdown(f"### {brief['company']} — {brief['date']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Revenue", _money(brief["revenue"]))
        c2.metric("Operating profit", _money(brief["profit"]))
        c3.metric("Margin", f"{brief['margin']:.1f}%")
        c4.metric("Open actions", brief["open_tasks"] + brief["pending_approvals"])
        st.markdown("#### Founder priorities")
        for i, item in enumerate(brief["priorities"], 1):
            st.markdown(f"**{i}.** {item}")
        st.markdown("#### Live rule-based signals")
        for alert in brief["alerts"]:
            st.info(f"**{alert['severity']} · {alert['area']}** — {alert['message']}")

    with tabs[1]:
        st.markdown("### Teach LeadLens how the business thinks")
        with st.form("memory_form", clear_on_submit=True):
            kind = st.selectbox("Memory type", ["strategic", "preference", "relationship", "lesson", "risk"])
            text = st.text_area("What should LeadLens remember?", placeholder="Corporate clients are our priority for the next two quarters.")
            importance = st.select_slider("Importance", ["Low", "Medium", "High", "Critical"], value="High")
            tags = st.text_input("Tags", placeholder="growth, corporate, Q4")
            if st.form_submit_button("Save memory", type="primary"):
                try:
                    remember(kind, text, [x.strip() for x in tags.split(",") if x.strip()], importance)
                    st.success("Business memory saved.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
        memory = load_memory()
        for section in ["strategic_memory", "preferences", "relationships", "lessons", "risks"]:
            with st.expander(section.replace("_", " ").title(), expanded=section == "strategic_memory"):
                items = memory.get(section, [])
                if not items:
                    st.caption("No entries yet.")
                for item in reversed(items[-12:]):
                    data = item.get("data", {})
                    st.markdown(f"**{data.get('importance', 'Medium')}** · {data.get('text', '')}")
                    st.caption(f"{item.get('created_at', '')} · {', '.join(data.get('tags', []))}")

    with tabs[2]:
        st.markdown("### Build an approval-ready campaign")
        with st.form("campaign_form"):
            name = st.text_input("Campaign name", placeholder="Mumbai Sneaker Revival")
            objective = st.text_input("Objective", value="Generate qualified leads")
            audience = st.text_input("Target audience", placeholder="Sneaker owners aged 18–35 in Mumbai")
            offer = st.text_input("Offer / CTA", value="Book a restoration consultation")
            theme = st.text_input("Campaign theme", placeholder="Restore, don't replace")
            budget = st.number_input("Budget (₹)", min_value=0.0, step=1000.0)
            days = st.slider("Duration", 3, 30, 14)
            if st.form_submit_button("Generate campaign", type="primary"):
                campaign = create_campaign({"name": name, "objective": objective, "audience": audience, "offer": offer, "theme": theme, "budget": budget, "days": days})
                st.session_state["new_campaign_id"] = campaign["id"]
                st.success("Campaign generated and sent for approval.")
                st.rerun()
        campaigns = load_memory().get("campaigns", [])
        for campaign in reversed(campaigns[-8:]):
            data = campaign.get("data", {})
            with st.expander(f"{data.get('name', 'Campaign')} · {data.get('status', 'Draft')}"):
                st.write(f"**Objective:** {data.get('objective')}  ")
                st.write(f"**Audience:** {data.get('audience')}  ")
                st.write(f"**Budget:** {_money(float(data.get('budget', 0) or 0))}")
                st.markdown("**Strategy**")
                for item in data.get("strategy", []): st.markdown(f"- {item}")
                st.markdown("**Sample captions**")
                for item in data.get("captions", []): st.code(item)
                st.dataframe(data.get("calendar", []), use_container_width=True, hide_index=True)
                st.download_button("Export campaign JSON", json.dumps(campaign, indent=2), f"{campaign.get('id')}.json", "application/json", key=f"dl_{campaign.get('id')}")
                if data.get("status") != "Active" and st.button("Queue activation for approval", key=f"activate_{campaign.get('id')}"):
                    propose_action("activate_campaign", {"campaign_id": campaign.get("id"), "department": "Marketing"}, "Medium")
                    st.success("Activation queued.")

    with tabs[3]:
        st.markdown("### Proactive business monitoring")
        if st.button("Run monitoring scan", type="primary"):
            alerts = run_monitors(persist=True)
            st.session_state["latest_alerts"] = alerts
        alerts = st.session_state.get("latest_alerts") or run_monitors(persist=False)
        for alert in alerts:
            method = st.error if alert["severity"] == "Critical" else st.warning if alert["severity"] in {"High", "Medium"} else st.info
            method(f"**{alert['area']} · {alert['severity']}** — {alert['message']}")

    with tabs[4]:
        st.markdown("### Controlled execution engine")
        with st.form("action_form"):
            action_type = st.selectbox("Action", ["create_task", "draft_email", "record_decision"])
            title = st.text_input("Title / subject")
            details = st.text_area("Details")
            department = st.selectbox("Department", ["Chief of Staff", "Sales", "Marketing", "Finance", "Operations", "HR"])
            risk = st.selectbox("Risk level", ["Low", "Medium", "High"])
            if st.form_submit_button("Prepare for approval", type="primary"):
                if action_type == "create_task": payload = {"title": title, "notes": details, "department": department, "priority": risk}
                elif action_type == "draft_email": payload = {"subject": title, "body": details, "department": department}
                else: payload = {"title": title, "reason": details, "impact": risk, "department": department}
                propose_action(action_type, payload, risk)
                st.success("Action prepared. It has not been executed yet.")
                st.rerun()
        runs = load_memory().get("automation_runs", [])
        for run in reversed(runs[-10:]):
            data = run.get("data", {})
            with st.container(border=True):
                st.markdown(f"**{data.get('action_type', '').replace('_', ' ').title()}** · {data.get('status')}")
                st.json(data.get("payload", {}), expanded=False)
                if data.get("status") == "Awaiting Approval":
                    if st.button("Approve and execute", key=f"exec_{run.get('id')}", type="primary"):
                        result = execute_action(run.get("id"))
                        st.success(result.get("message", "Executed"))
                        st.rerun()
                elif data.get("result"):
                    st.caption(data.get("result", {}).get("message", "Completed"))

    with tabs[5]:
        st.markdown("### Integration Hub")
        st.warning("This build includes secure connection records and integration architecture. Live OAuth/API execution requires credentials from each business account.")
        for integration in list_integrations():
            with st.expander(f"{integration['name']} · {integration['status']}"):
                st.write(integration["description"])
                with st.form(f"integration_{integration['name']}"):
                    identifier = st.text_input("Account / workspace identifier", key=f"id_{integration['name']}")
                    note = st.text_input("Configuration note", key=f"note_{integration['name']}")
                    if st.form_submit_button("Save configuration"):
                        save_connection(integration["name"], {"identifier": identifier, "note": note})
                        st.success("Configuration recorded. No external action was performed.")
                        st.rerun()
