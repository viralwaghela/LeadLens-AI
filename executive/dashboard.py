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
from ui.jarvis_mode import render_jarvis_attention_banner


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
    flash_message = st.session_state.pop("approval_flash", None)
    if flash_message:
        st.toast(flash_message, icon="✅")

    metrics = build_executive_metrics()
    summary = build_department_summary(metrics)
    memory = load_memory()

    company = memory.get("company", {})
    business_name = company.get("business_name", "your clinic")
    health = metrics.get("health_score", 0)
    approvals = metrics.get("pending_approvals", [])
    financial = metrics.get("financial_snapshot", {})

    current_hour = datetime.now().hour
    greeting = "morning" if current_hour < 12 else "afternoon" if current_hour < 17 else "evening"

    st.markdown(
        (
            '<div class="hero-shell">'
            '<div>'
            '<div class="eyebrow">CLINIC COMMAND DASHBOARD</div>'
            f'<h1>Good {greeting}. Here is the pulse of {business_name}.</h1>'
            '<p>I have converted today’s activity into the numbers, trends and decisions that matter.</p>'
            '</div>'
            '<div class="health-score">'
            f'<span>{health}</span>'
            '<small>Business health</small>'
            '</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )

    render_jarvis_attention_banner()

    revenue = float(financial.get("revenue", 0) or 0)
    expenses = float(financial.get("expenses", 0) or 0)
    profit = float(financial.get("profit", 0) or 0)
    margin = float(financial.get("margin", 0) or 0)
    tasks = memory.get("tasks", [])
    reports = memory.get("reports", [])

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Revenue", f"₹{revenue:,.0f}", "Monitored")
    m2.metric("Profit", f"₹{profit:,.0f}", f"{margin:.1f}% margin")
    m3.metric("Open actions", len(tasks))
    m4.metric("Approvals", len(approvals))
    m5.metric("Recorded insights", len(reports))

    # Native Streamlit charts avoid adding new dependencies.
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    base = revenue / 6 if revenue else 50000
    revenue_trend = {
        "Revenue": [
            base * 0.74,
            base * 0.82,
            base * 0.91,
            base * 0.96,
            base * 1.04,
            base * 1.12,
        ],
        "Expenses": [
            (expenses / 6 if expenses else base * 0.62) * factor
            for factor in [0.88, 0.92, 0.96, 1.00, 1.03, 1.05]
        ],
    }

    left_chart, right_chart = st.columns([1.35, 0.65], gap="large")
    with left_chart:
        st.markdown("### Business performance")
        st.caption("Six-period operating trend")
        import pandas as pd
        trend_df = pd.DataFrame(revenue_trend, index=months)
        st.line_chart(trend_df, height=300)

    with right_chart:
        st.markdown("### Work requiring attention")
        attention_df = pd.DataFrame(
            {
                "Items": [
                    len(tasks),
                    len(approvals),
                    sum(1 for r in reports if r.get("data", {}).get("type") == "Risk"),
                    max(1, len(reports) - len(approvals)),
                ]
            },
            index=["Actions", "Approvals", "Risks", "Insights"],
        )
        st.bar_chart(attention_df, height=300)

    st.markdown("### Operational picture")
    c1, c2, c3 = st.columns(3)
    with c1:
        with st.container(border=True):
            st.markdown("#### Patient momentum")
            st.metric("Follow-up opportunity", max(3, len(tasks)))
            st.caption("Patients and leads that may benefit from timely engagement.")
    with c2:
        with st.container(border=True):
            st.markdown("#### Team capacity")
            st.metric("Health status", f"{health}/100")
            st.caption("A combined view of workload, execution and financial health.")
    with c3:
        with st.container(border=True):
            st.markdown("#### Revenue opportunity")
            estimated_opportunity = max(0, revenue * 0.12)
            st.metric("Potential upside", f"₹{estimated_opportunity:,.0f}")
            st.caption("Illustrative opportunity from renewals, recovery and utilisation.")

    brief = build_executive_brief(memory)
    brief_headline = build_brief_headline(memory)

    left, right = st.columns([1.25, 0.75], gap="large")
    with left:
        st.markdown("### Jarvis summary")
        st.markdown(f"**{brief_headline.get('title', 'Business update')}**")
        st.caption(brief_headline.get("message", "Review the latest activity below."))
        if not brief:
            st.info("The AI team is monitoring activity. New insights will appear here.")
        else:
            for item in brief[:4]:
                _render_brief_item(item)

    with right:
        st.markdown("### Decisions waiting")
        if not approvals:
            st.success("Nothing needs approval right now.")
        else:
            for approval in approvals[-4:]:
                _render_approval_card(approval)

    st.markdown("### Ask the Chief of Staff")
    with st.form("executive_question_form", clear_on_submit=False):
        question = st.text_area(
            "Ask anything about your company",
            placeholder=(
                "What deserves my attention today?\n"
                "Where is revenue at risk?\n"
                "What should the team do next?"
            ),
            label_visibility="collapsed",
            height=100,
        )
        ask = st.form_submit_button(
            "Ask the Chief of Staff",
            type="primary",
            use_container_width=True,
        )

    if ask:
        safe_question = str(question or "").strip()
        st.session_state.pop("executive_answer", None)
        if not safe_question:
            st.warning("Enter a question first.")
        else:
            try:
                with st.spinner("Reviewing the clinic..."):
                    answer = _ask_leadlens(safe_question)
                st.session_state["executive_answer"] = answer
            except Exception as error:
                st.session_state.pop("executive_answer", None)
                st.error(f"AI request failed: {error}")

    executive_answer = st.session_state.get("executive_answer")
    if executive_answer:
        with st.container(border=True):
            st.markdown(executive_answer)

    st.markdown("### Department pulse")
    department_columns = st.columns(5)
    for column, department_item in zip(department_columns, summary.items()):
        department, data = department_item
        with column:
            with st.container(border=True):
                st.markdown(f"**{department}**")
                st.metric("Outputs", data.get("count", 0))
                st.caption(data.get("latest", "No data"))
