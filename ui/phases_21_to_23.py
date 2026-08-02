from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from services.agent_collaboration_v23 import council_sessions, run_agent_council
from services.integration_manager_v21 import (
    decide_item,
    execute_item,
    execution_rows,
    integration_statuses,
    prepare_execution,
)
from services.learning_memory_v22 import learning_summary, record_learning_outcome, save_business_preference


def show_phase21_integrations():
    st.markdown('<div class="eyebrow">CONNECTED SERVICES</div>', unsafe_allow_html=True)
    st.title("Connected Services")
    st.caption("Connect clinic communication and scheduling tools. Every external action requires approval before execution.")
    cols = st.columns(3)
    for col, status in zip(cols, integration_statuses()):
        col.metric(status["provider"], status["mode"].upper())
        col.caption("Configured" if status["configured"] else "Credentials not configured; safe simulation mode")

    tab1, tab2, tab3, tab4 = st.tabs(["Calendar", "Gmail", "WhatsApp", "Execution Queue"])
    with tab1:
        with st.form("calendar_execution"):
            summary = st.text_input("Event title")
            start = st.datetime_input("Start", value=datetime.now() + timedelta(days=1))
            end = st.datetime_input("End", value=datetime.now() + timedelta(days=1, hours=1))
            description = st.text_area("Description")
            if st.form_submit_button("Prepare Calendar execution"):
                item = prepare_execution("calendar", "create_event", {"summary": summary, "start": start.isoformat(), "end": end.isoformat(), "description": description, "timezone": "Asia/Kolkata"}, f"Create calendar event: {summary}")
                st.success(f"Prepared {item['id']}. Approve it in the Execution Queue.")
    with tab2:
        with st.form("gmail_execution"):
            to = st.text_input("Recipient")
            subject = st.text_input("Subject")
            body = st.text_area("Email body")
            action = st.selectbox("Action", ["create_draft", "send_email"])
            if st.form_submit_button("Prepare Gmail execution"):
                item = prepare_execution("gmail", action, {"to": to, "subject": subject, "body": body}, f"{action.replace('_',' ').title()}: {subject}")
                st.success(f"Prepared {item['id']}. Approve it in the Execution Queue.")
    with tab3:
        with st.form("whatsapp_execution"):
            to = st.text_input("WhatsApp number with country code")
            body = st.text_area("Message")
            if st.form_submit_button("Prepare WhatsApp execution"):
                item = prepare_execution("whatsapp", "send_text", {"to": to, "body": body}, f"Send WhatsApp message to {to}")
                st.success(f"Prepared {item['id']}. Approve it in the Execution Queue.")
    with tab4:
        rows = execution_rows()
        if not rows:
            st.info("No prepared executions.")
        for row in rows[:50]:
            with st.expander(f"{row['title']} · {row['status']}"):
                st.write({"provider": row["provider"], "action": row["action"], "approval": row["approval_status"], "created_at": row["created_at"]})
                if row["approval_status"].lower() == "pending":
                    a, b = st.columns(2)
                    if a.button("Approve", key=f"approve_{row['id']}"):
                        decide_item(row["id"], "Approved"); st.rerun()
                    if b.button("Reject", key=f"reject_{row['id']}"):
                        decide_item(row["id"], "Rejected"); st.rerun()
                if row["approval_status"].lower() == "approved" and row["status"] == "Approved":
                    if st.button("Execute approved action", key=f"execute_{row['id']}", type="primary"):
                        result = execute_item(row["id"])
                        st.success(result) if result.get("success") else st.error(result)
                        st.rerun()
                if row.get("result"):
                    st.json(row["result"])


def show_phase22_learning_memory():
    st.markdown('<div class="eyebrow">BUSINESS LEARNING</div>', unsafe_allow_html=True)
    st.title("Business Insights")
    summary = learning_summary()
    a, b, c = st.columns(3)
    a.metric("Measured outcomes", len(summary["outcomes"]))
    b.metric("Learned patterns", len(summary["patterns"]))
    c.metric("Business preferences", len(summary["preferences"]))
    tab1, tab2, tab3 = st.tabs(["Record Outcome", "Preferences", "Learned Patterns"])
    with tab1:
        with st.form("learning_outcome"):
            action_type = st.text_input("Action type", "Patient recovery")
            channel = st.selectbox("Channel", ["WhatsApp", "Gmail", "Phone", "Calendar", "Other"])
            audience = st.text_input("Audience", "Inactive patients")
            contacts = st.number_input("Contacts", min_value=0, value=0)
            conversions = st.number_input("Conversions", min_value=0, value=0)
            revenue = st.number_input("Revenue generated", min_value=0.0, value=0.0, step=1000.0)
            cost = st.number_input("Cost", min_value=0.0, value=0.0, step=100.0)
            notes = st.text_area("Notes")
            if st.form_submit_button("Save and learn"):
                record_learning_outcome(action_type, channel, audience, contacts, conversions, revenue, cost, notes); st.success("Outcome recorded and patterns recalculated."); st.rerun()
    with tab2:
        with st.form("business_preference"):
            key = st.text_input("Preference name")
            value = st.text_input("Preference value")
            reason = st.text_area("Reason")
            if st.form_submit_button("Save preference"):
                save_business_preference(key, value, reason); st.success("Preference saved."); st.rerun()
        if summary["preferences"]:
            st.dataframe(summary["preferences"], use_container_width=True)
    with tab3:
        if summary["patterns"]:
            st.dataframe(summary["patterns"], use_container_width=True)
        else:
            st.info("Record outcomes to create learned patterns.")


def show_phase23_agent_collaboration():
    st.markdown('<div class="eyebrow">COLLABORATIVE INTELLIGENCE</div>', unsafe_allow_html=True)
    st.title("AI Team")
    question = st.text_area("Business question", "What should the clinic prioritise this week?")
    if st.button("Ask the AI team", type="primary"):
        st.session_state["v23_council"] = run_agent_council(question)
    session = st.session_state.get("v23_council")
    if session:
        for response in session["agents"]:
            with st.expander(response["agent"], expanded=True):
                st.write("**Finding:**", response["finding"])
                st.write("**Recommendation:**", response["recommendation"])
                st.write("**Risk:**", response["risk"])
        st.subheader("Recommended decision")
        st.success(session["synthesis"]["decision"])
        for action in session["synthesis"]["supporting_actions"]:
            st.write(f"- {action}")
        st.caption(session["synthesis"]["management_rule"])
    history = council_sessions()
    if history:
        with st.expander("Previous AI team reviews"):
            st.dataframe([{"created_at": x["created_at"], "question": x["question"], "decision": x["synthesis"]["decision"]} for x in history[:30]], use_container_width=True)
