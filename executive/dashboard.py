import json
from datetime import datetime

import streamlit as st

from core.memory import load_memory, update_approval_status
from executive.brief import (
    build_brief_headline,
    build_executive_brief,
)
from executive.metrics import build_executive_metrics
from executive.summaries import build_department_summary
from services.ai import generate_ai_response


def _ask_leadlens(question):
    memory = load_memory()

    now = datetime.now()
    current_date = now.strftime("%B %d, %Y")
    current_date_iso = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%I:%M %p")

    memory_json = json.dumps(
        memory,
        indent=2,
        ensure_ascii=False,
        default=str,
    )

    prompt = f"""
You are LeadLens, an executive AI advisor.

Current date:
{current_date}

Business memory:
{memory_json}

User question:
{question}

Instructions:

1. Use ONLY information present in business memory.
2. Never invent events, dates, approvals or metrics.
3. Treat something as happening today ONLY if its date starts with:

{current_date_iso}

4. Ignore historical records unless the user explicitly asks for history.

5. Never mention:
- created_at
- JSON
- database records
- report IDs
- internal memory structures

6. Never expose chain-of-thought reasoning.

7. Answer like an executive chief of staff.

8. Use this format:

### Title

• key update
• key update
• key update

### Pending
• pending item
OR
• No pending items.

### Priority
• single recommendation

9. Keep responses concise.

10. Avoid technical wording.

Return only the final answer.
"""

    return generate_ai_response(
    prompt,
    system_prompt=
    (
        "You are LeadLens, an executive business advisor. "
        "Your answers are concise, actionable, and suitable "
        "for CEOs and founders. "
        "Never reveal internal reasoning."
    )
)


def _render_brief_item(item):
    department = item.get("department", "Business")
    title = item.get("title", "Activity recorded")
    status = item.get("status", "Activity")
    created_at = item.get("created_at", "Date unavailable")

    with st.container(border=True):
        top_left, top_right = st.columns([4, 1])

        with top_left:
            st.markdown(f"**{department}**")
            st.write(title)
            st.caption(f"Recorded: {created_at}")

        with top_right:
            if status in ("Completed", "Approved"):
                st.success(status)
            elif status in ("Rejected", "Needs attention"):
                st.error(status)
            elif status == "Pending":
                st.warning(status)
            else:
                st.info(status)


def _render_approval_card(approval):
    approval_data = approval.get("data", {})
    approval_id = approval.get("id")

    if not approval_id:
        return

    title = approval_data.get("title", "Approval")
    department = approval_data.get("department", "General")
    risk_level = approval_data.get("risk_level", "Medium")

    with st.container(border=True):
        st.write(f"**{title}**")
        st.caption(f"{department} · {risk_level} risk")

        approve_column, reject_column = st.columns(2)

        if approve_column.button(
            "Approve",
            key=f"approve_{approval_id}",
            use_container_width=True,
        ):
            result = update_approval_status(
                approval_id,
                "Approved",
            )

            if result:
                st.session_state["approval_flash"] = (
                    "Approval approved successfully."
                )
                st.rerun()
            else:
                st.error("Approval could not be found.")

        if reject_column.button(
            "Reject",
            key=f"reject_{approval_id}",
            use_container_width=True,
        ):
            result = update_approval_status(
                approval_id,
                "Rejected",
            )

            if result:
                st.session_state["approval_flash"] = (
                    "Approval rejected successfully."
                )
                st.rerun()
            else:
                st.error("Approval could not be found.")


def show_executive_dashboard():
    flash_message = st.session_state.pop(
        "approval_flash",
        None,
    )

    if flash_message:
        st.toast(flash_message, icon="✅")

    metrics = build_executive_metrics()
    summary = build_department_summary(metrics)
    memory = load_memory()

    company = memory.get("company", {})
    business_name = company.get(
        "business_name",
        "your business",
    )

    health = metrics.get("health_score", 0)
    approvals = metrics.get("pending_approvals", [])
    financial = metrics.get("financial_snapshot", {})

    current_hour = datetime.now().hour

    if current_hour < 12:
        greeting = "morning"
    elif current_hour < 17:
        greeting = "afternoon"
    else:
        greeting = "evening"

    hero_html = (
        '<div class="hero-shell">'
        '<div>'
        '<div class="eyebrow">EXECUTIVE WORKSPACE</div>'
        f'<h1>Good {greeting}, {business_name}.</h1>'
        '<p>Your company has been reviewed. '
        'Here is what needs your attention.</p>'
        '</div>'
        '<div class="health-score">'
        f'<span>{health}</span>'
        '<small>Business health</small>'
        '</div>'
        '</div>'
    )

    st.markdown(
        hero_html,
        unsafe_allow_html=True,
    )

    revenue = financial.get("revenue", 0)
    expenses = financial.get("expenses", 0)
    profit = financial.get("profit", 0)
    margin = financial.get("margin", 0)

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)

    metric_1.metric("Revenue", f"₹{revenue:,.0f}")
    metric_2.metric("Expenses", f"₹{expenses:,.0f}")
    metric_3.metric("Profit", f"₹{profit:,.0f}")
    metric_4.metric("Margin", f"{margin:.1f}%")

    brief = build_executive_brief(memory)
    brief_headline = build_brief_headline(memory)

    left, right = st.columns(
        [1.3, 1],
        gap="large",
    )

    with left:
        st.markdown("### Latest executive brief")

        st.markdown(
            f"**{brief_headline.get('title', 'Business update')}**"
        )

        st.caption(
            brief_headline.get(
                "message",
                "Review the latest activity below.",
            )
        )

        if not brief:
            st.info(
                "Generate department outputs to build your executive brief."
            )
        else:
            for item in brief:
                _render_brief_item(item)

    with right:
        st.markdown("### Decisions waiting")

        if not approvals:
            st.success("Nothing needs approval right now.")
        else:
            for approval in approvals[-4:]:
                _render_approval_card(approval)

    # Ask LeadLens must remain here, inside this function.
    st.markdown("### Ask LeadLens")

    with st.form(
        "executive_question_form",
        clear_on_submit=False,
    ):
        question = st.text_area(
            "Ask anything about your company",
            placeholder=(
                "What happened today?\n"
                "What approvals are pending?\n"
                "What should I prioritize?"
            ),
            label_visibility="collapsed",
            height=100,
        )

        ask = st.form_submit_button(
            "Ask LeadLens",
            type="primary",
            use_container_width=True,
        )

    if ask:
        safe_question = str(question or "").strip()

        st.session_state.pop(
            "executive_answer",
            None,
        )

        if not safe_question:
            st.warning("Enter a question first.")
        else:
            try:
                with st.spinner(
                    "Reviewing your business memory..."
                ):
                    answer = _ask_leadlens(
                        safe_question
                    )

                st.session_state[
                    "executive_answer"
                ] = answer

            except Exception as error:
                st.session_state.pop(
                    "executive_answer",
                    None,
                )

                st.error(
                    f"AI request failed: {error}"
                )

    executive_answer = st.session_state.get(
        "executive_answer"
    )

    if executive_answer:
        with st.container(border=True):
            st.markdown(executive_answer)

    st.markdown("### Department overview")

    department_columns = st.columns(5)

    for column, department_item in zip(
        department_columns,
        summary.items(),
    ):
        department, data = department_item

        with column:
            with st.container(border=True):
                st.markdown(f"**{department}**")

                st.metric(
                    "Outputs",
                    data.get("count", 0),
                )

                st.caption(
                    data.get(
                        "latest",
                        "No data",
                    )
                )

                st.caption(
                    "Last updated: "
                    f"{data.get('date', 'N/A')}"
                )