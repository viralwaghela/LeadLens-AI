import json
import streamlit as st

from services.ai import generate_ai_response
from services.json_utils import parse_json_response
from core.memory import (
    load_memory,
    add_task,
    add_approval,
    add_decision,
    add_daily_log,
    add_memory_entry,
)


def build_ceo_update_prompt(memory, ceo_update):
    return f"""
You are LeadLens AI, an AI Chief Operating Officer.

The business owner has given you an update about what happened in the business.

Your job:
- Understand the update.
- Update business memory.
- Create useful tasks.
- Create decisions if needed.
- Create approvals if needed.
- Create a daily log.
- Identify risks and opportunities.

Return ONLY valid JSON.
Do NOT wrap JSON in markdown.
Do NOT explain anything outside JSON.

Business Memory:
{json.dumps(memory, indent=2)}

Owner Update:
{ceo_update}

Return exactly this JSON structure:

{{
    "summary": "Short summary of what happened.",
    "tasks": [
        {{
            "title": "Task title",
            "department": "Marketing",
            "priority": "High"
        }}
    ],
    "decisions": [
        {{
            "title": "Decision title",
            "reason": "Reason for decision",
            "impact": "Medium"
        }}
    ],
    "approvals": [
        {{
            "title": "Approval title",
            "department": "Finance",
            "risk_level": "Medium"
        }}
    ],
    "risks": [
        {{
            "title": "Risk title",
            "department": "Operations",
            "severity": "Medium",
            "reason": "Why this is risky"
        }}
    ],
    "opportunities": [
        {{
            "title": "Opportunity title",
            "department": "Sales",
            "potential_impact": "High",
            "reason": "Why this is an opportunity"
        }}
    ],
    "daily_log": "Daily log entry"
}}
"""



def build_local_fallback(ceo_update):
    text=ceo_update.strip()
    low=text.lower()
    tasks=[]; risks=[]; opportunities=[]; approvals=[]; decisions=[]
    if any(x in low for x in ["booking", "patient"]):
        tasks.append({"title":"Review patient booking and follow-up pipeline","department":"Operations","priority":"High"})
    if any(x in low for x in ["renewal", "package"]):
        tasks.append({"title":"Contact patients with packages due for renewal","department":"Customer Success","priority":"High"})
    if any(x in low for x in ["overload", "full", "capacity"]):
        risks.append({"title":"Therapist capacity pressure","department":"Operations","severity":"High","reason":"Current demand may exceed safe delivery capacity."})
        decisions.append({"title":"Review therapist capacity and slot redistribution","reason":"Protect service quality while meeting demand.","impact":"High"})
    if any(x in low for x in ["corporate", "enquir"]):
        opportunities.append({"title":"Corporate wellness growth","department":"Sales","potential_impact":"High","reason":"Corporate interest is increasing."})
        tasks.append({"title":"Prepare corporate wellness proposal","department":"Sales","priority":"High"})
    if any(x in low for x in ["cost", "budget", "marketing"]):
        approvals.append({"title":"Review marketing budget and return","department":"Marketing","risk_level":"Medium"})
    return {"summary":text[:300],"tasks":tasks,"decisions":decisions,"approvals":approvals,"risks":risks,"opportunities":opportunities,"daily_log":text}

def parse_json(response):
    return parse_json_response(response)


def save_ceo_update_to_memory(result):
    for task in result.get("tasks", []):
        add_task(
            task.get("title", ""),
            task.get("department", "Operations"),
            task.get("priority", "Medium")
        )

    for decision in result.get("decisions", []):
        add_decision(
            decision.get("title", ""),
            decision.get("reason", ""),
            decision.get("impact", "Medium")
        )

    for approval in result.get("approvals", []):
        add_approval(
            approval.get("title", ""),
            approval.get("department", "Operations"),
            approval.get("risk_level", "Medium")
        )

    for risk in result.get("risks", []):
        add_memory_entry("reports", {
            "type": "Risk",
            "title": risk.get("title", ""),
            "department": risk.get("department", ""),
            "severity": risk.get("severity", ""),
            "reason": risk.get("reason", "")
        })

    for opportunity in result.get("opportunities", []):
        add_memory_entry("reports", {
            "type": "Opportunity",
            "title": opportunity.get("title", ""),
            "department": opportunity.get("department", ""),
            "potential_impact": opportunity.get("potential_impact", ""),
            "reason": opportunity.get("reason", "")
        })

    if result.get("daily_log"):
        add_daily_log(result.get("daily_log"))


def display_items(title, items, fields):
    st.subheader(title)

    if not items:
        st.info("None generated.")
        return

    for item in items:
        st.write(f"**{item.get('title', 'Untitled')}**")

        for field in fields:
            st.write(f"**{field.replace('_', ' ').title()}:** {item.get(field, '')}")

        st.divider()


def show_ceo_command_center():
    st.subheader("🧠 CEO Command Center")

    st.write("Tell your AI COO what happened in the business. It will update memory, create tasks, decisions, approvals, risks and opportunities.")

    ceo_update = st.text_area(
        "What happened in your business?",
        height=180,
        placeholder="Example: Patient bookings dropped this week. We received two new Google reviews. Meta ads are expensive. We want to promote corporate posture workshops."
    )

    process_update = st.button("Update Business Memory", use_container_width=True)

    if process_update:
        if ceo_update.strip() == "":
            st.error("Please enter a business update.")
            return

        memory = load_memory()
        prompt = build_ceo_update_prompt(memory, ceo_update)

        with st.spinner("AI COO is processing your business update..."):
            response = generate_ai_response(
                prompt,
                "You are an AI Chief Operating Officer."
            )

        result = parse_json(response) or build_local_fallback(ceo_update)

        if result:
            st.success("Business update processed.")

            st.subheader("Executive Summary")
            st.info(result.get("summary", ""))

            display_items(
                "Generated Tasks",
                result.get("tasks", []),
                ["department", "priority"]
            )

            display_items(
                "Decisions",
                result.get("decisions", []),
                ["reason", "impact"]
            )

            display_items(
                "Approvals",
                result.get("approvals", []),
                ["department", "risk_level"]
            )

            display_items(
                "Risks",
                result.get("risks", []),
                ["department", "severity", "reason"]
            )

            display_items(
                "Opportunities",
                result.get("opportunities", []),
                ["department", "potential_impact", "reason"]
            )

            if st.button("Save This Update to Memory", use_container_width=True):
                save_ceo_update_to_memory(result)
                st.success("Saved to business memory.")
                st.rerun()

        else:
            st.error("AI returned invalid JSON.")
            st.text_area("Raw AI Output", response, height=400)