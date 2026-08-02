"""LeadLens AI Chief of Staff workspace."""
from __future__ import annotations
import json
from datetime import datetime, timedelta

import streamlit as st
from agents.chief_of_staff import ChiefOfStaff
from services.integration_manager_v21 import prepare_execution
from services.jarvis_memory import (
    record_recommendation_outcome,
    track_recommendation,
)

PROMPTS = [
    "Give me today's executive briefing.",
    "Review sales and marketing together.",
    "Can we afford to increase the marketing budget?",
    "What are the biggest risks in the business?",
    "What should the team focus on this week?",
    "Review operations, HR, and finance together.",
]


def _show_action_preparation(tracked_id: str) -> None:
    st.divider()
    st.markdown("#### Prepare an action for approval")
    st.caption(
        "Jarvis will store the exact payload for review. Preparing an action "
        "does not approve or execute it."
    )
    action_label = st.selectbox(
        "Action type",
        [
            "Gmail draft",
            "Gmail send",
            "WhatsApp message",
            "Calendar event",
        ],
        key="jarvis_prepared_action_type",
    )

    if action_label in {"Gmail draft", "Gmail send"}:
        with st.form(f"jarvis_prepare_{action_label.replace(' ', '_')}"):
            to = st.text_input("Recipient email")
            subject = st.text_input("Subject")
            body = st.text_area("Email body")
            impact = st.text_input(
                "Expected impact",
                placeholder="What should this action achieve?",
            )
            submitted = st.form_submit_button("Review exact payload")
        provider = "gmail"
        action = (
            "create_draft" if action_label == "Gmail draft"
            else "send_email"
        )
        payload = {"to": to, "subject": subject, "body": body}
        title = f"{action_label}: {subject or 'Untitled email'}"
    elif action_label == "WhatsApp message":
        with st.form("jarvis_prepare_whatsapp"):
            to = st.text_input("Number with country code")
            body = st.text_area("Message")
            impact = st.text_input(
                "Expected impact",
                placeholder="What should this action achieve?",
            )
            submitted = st.form_submit_button("Review exact payload")
        provider = "whatsapp"
        action = "send_text"
        payload = {"to": to, "body": body}
        title = f"WhatsApp message to {to or 'recipient'}"
    else:
        default_start = datetime.now() + timedelta(days=1)
        with st.form("jarvis_prepare_calendar"):
            summary = st.text_input("Event title")
            start = st.datetime_input("Start", value=default_start)
            end = st.datetime_input(
                "End",
                value=default_start + timedelta(hours=1),
            )
            description = st.text_area("Description")
            impact = st.text_input(
                "Expected impact",
                placeholder="What should this action achieve?",
            )
            submitted = st.form_submit_button("Review exact payload")
        provider = "calendar"
        action = "create_event"
        payload = {
            "summary": summary,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "description": description,
            "timezone": "Asia/Kolkata",
        }
        title = f"Calendar event: {summary or 'Untitled event'}"

    if submitted:
        try:
            item = prepare_execution(
                provider,
                action,
                payload,
                title,
                recommendation_id=tracked_id,
                impact=impact,
            )
        except ValueError as error:
            st.error(str(error))
            return
        st.session_state.latest_prepared_execution = item

    item = st.session_state.get("latest_prepared_execution")
    if item and item.get("recommendation_id") == tracked_id:
        st.markdown("**Exact payload awaiting review**")
        st.json(item.get("payload", {}))
        st.info(
            f"{item['id']} is prepared and awaiting approval. "
            "Nothing has been executed."
        )
        st.caption(
            "Open Action Center → Prepared actions to approve, reject, "
            "and—only after approval—execute it."
        )


def _show_memory_controls() -> None:
    draft = st.session_state.get("last_jarvis_recommendation")
    if not draft:
        return
    with st.expander("Track this recommendation and its result"):
        st.caption(
            "Nothing is saved until you choose Track recommendation. "
            "Recording an outcome also requires an explicit submission."
        )
        if st.button(
            "Track recommendation",
            key="track_latest_recommendation",
        ):
            tracked = track_recommendation(
                draft["question"],
                draft["recommendation"],
                agents=draft.get("agents", []),
            )
            st.session_state.tracked_jarvis_recommendation_id = tracked["id"]
            st.success(f"Tracked as {tracked['id']}.")

        tracked_id = st.session_state.get(
            "tracked_jarvis_recommendation_id"
        )
        if tracked_id:
            with st.form("jarvis_outcome_form"):
                result_value = st.selectbox(
                    "Result",
                    ["successful", "partial", "unsuccessful", "unknown"],
                )
                action_taken = st.text_input("Action taken")
                notes = st.text_area("What happened?")
                metric_name = st.text_input(
                    "Metric name (optional)",
                    placeholder="e.g. renewals",
                )
                metric_value = st.text_input(
                    "Metric value (optional)",
                    placeholder="e.g. 5",
                )
                if st.form_submit_button("Save measured outcome"):
                    metrics = (
                        {metric_name: metric_value}
                        if metric_name.strip() else {}
                    )
                    record_recommendation_outcome(
                        tracked_id,
                        result_value,
                        action_taken,
                        metrics=metrics,
                        notes=notes,
                    )
                    st.success("Outcome saved. Jarvis can use it next time.")
            _show_action_preparation(tracked_id)


def show_chief_of_staff_workspace() -> None:
    st.markdown('<div class="eyebrow">LEADLENS BUSINESS OS</div>', unsafe_allow_html=True)
    st.markdown("## AI Chief of Staff")
    st.caption("One conversation with your entire AI leadership team. LeadLens uses the business memory shared across every department.")
    if "chief_of_staff" not in st.session_state:
        st.session_state.chief_of_staff = ChiefOfStaff()
    if "chief_of_staff_messages" not in st.session_state:
        st.session_state.chief_of_staff_messages = []

    left, right = st.columns([5, 1])
    with left:
        st.markdown("**Active agents:** Chief of Staff · Sales · Marketing · Finance · Operations · HR · Analytics")
    with right:
        if st.button("Clear chat", use_container_width=True):
            st.session_state.chief_of_staff_messages = []
            st.rerun()

    if not st.session_state.chief_of_staff_messages:
        st.markdown("### Start with a management question")
        cols = st.columns(3)
        for i, prompt in enumerate(PROMPTS):
            with cols[i % 3]:
                if st.button(prompt, key=f"prompt_{i}", use_container_width=True):
                    st.session_state.pending_chief_query = prompt
                    st.rerun()

    for message in st.session_state.chief_of_staff_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    typed = st.chat_input("Ask your AI leadership team...")
    query = typed or st.session_state.pop("pending_chief_query", None)
    if not query:
        if st.session_state.chief_of_staff_messages:
            st.download_button(
                "Export conversation",
                json.dumps(st.session_state.chief_of_staff_messages, indent=2),
                "leadlens_chief_of_staff_conversation.json",
                "application/json",
            )
        _show_memory_controls()
        return

    st.session_state.chief_of_staff_messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)
    with st.chat_message("assistant"):
        with st.spinner("Coordinating your AI leadership team..."):
            result = {}
            try:
                result = st.session_state.chief_of_staff.process_query(
                    query,
                    conversation_history=st.session_state.chief_of_staff_messages[:-1],
                )
                answer = str(result.get("message", "No response was generated."))
            except Exception as error:
                answer = f"LeadLens could not process this request.\n\n`{error}`"
            st.markdown(answer)
            trace = result.get("trace", {})
            if trace:
                with st.expander("Consultation trace", expanded=False):
                    st.caption(
                        "Read-only review · No external actions were executed"
                    )
                    st.write(
                        "**Consulted:** "
                        + ", ".join(trace.get("selected_agents", []))
                    )
                    for agent, tools in trace.get("tools_used", {}).items():
                        st.caption(f"{agent}: {', '.join(tools)}")
    st.session_state.chief_of_staff_messages.append({"role": "assistant", "content": answer})
    if result.get("success"):
        st.session_state.pop("tracked_jarvis_recommendation_id", None)
        st.session_state.last_jarvis_recommendation = {
            "question": query,
            "recommendation": answer,
            "agents": result.get("trace", {}).get("selected_agents", []),
        }

    _show_memory_controls()
