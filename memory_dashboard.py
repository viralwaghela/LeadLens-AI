import streamlit as st

from core.memory import (
    get_memory_section,
    add_task,
    complete_task,
    add_decision,
    add_approval,
    add_daily_log,
)


def show_memory_dashboard():

    st.subheader("🧠 Business Memory")

    tab1, tab2, tab3, tab4 = st.tabs([
        "✅ Tasks",
        "📌 Decisions",
        "🛡️ Approvals",
        "📓 Daily Logs"
    ])

    with tab1:
        st.markdown("### Add Task")

        with st.form("add_task_form"):
            task_title = st.text_input("Task Title")
            department = st.selectbox(
                "Department",
                [
                    "Marketing",
                    "Sales",
                    "Finance",
                    "HR",
                    "Operations",
                    "Customer Success",
                    "Analytics",
                    "Content"
                ]
            )
            priority = st.selectbox(
                "Priority",
                ["Low", "Medium", "High"]
            )

            submitted = st.form_submit_button("Add Task")

            if submitted:
                if task_title.strip():
                    add_task(task_title, department, priority)
                    st.success("Task added.")
                    st.rerun()
                else:
                    st.error("Please enter a task title.")

        st.markdown("### Pending Tasks")

        tasks = get_memory_section("tasks")

        if tasks:
            for task in tasks:
                data = task.get("data", {})

                col1, col2 = st.columns([4, 1])

                with col1:
                    st.write(
                        f"**{data.get('title')}**  \n"
                        f"Department: {data.get('department')} | "
                        f"Priority: {data.get('priority')} | "
                        f"Status: {data.get('status')}"
                    )

                with col2:
                    if st.button("Complete", key=task.get("id")):
                        complete_task(task.get("id"))
                        st.rerun()
        else:
            st.info("No pending tasks.")

    with tab2:
        st.markdown("### Add Decision")

        with st.form("add_decision_form"):
            title = st.text_input("Decision Title")
            reason = st.text_area("Reason")
            impact = st.selectbox(
                "Impact",
                ["Low", "Medium", "High"]
            )

            submitted = st.form_submit_button("Save Decision")

            if submitted:
                if title.strip() and reason.strip():
                    add_decision(title, reason, impact)
                    st.success("Decision saved.")
                    st.rerun()
                else:
                    st.error("Please enter decision title and reason.")

        st.markdown("### Decision History")

        decisions = get_memory_section("decisions")

        if decisions:
            for decision in decisions:
                data = decision.get("data", {})
                st.write(
                    f"**{data.get('title')}**  \n"
                    f"Impact: {data.get('impact')}  \n"
                    f"Reason: {data.get('reason')}  \n"
                    f"Created: {decision.get('created_at')}"
                )
                st.divider()
        else:
            st.info("No decisions saved yet.")

    with tab3:
        st.markdown("### Add Approval Request")

        with st.form("add_approval_form"):
            title = st.text_input("Approval Title")
            department = st.selectbox(
                "Approval Department",
                [
                    "Marketing",
                    "Sales",
                    "Finance",
                    "HR",
                    "Operations",
                    "Customer Success",
                    "Analytics",
                    "Content"
                ]
            )
            risk_level = st.selectbox(
                "Risk Level",
                ["Low", "Medium", "High"]
            )

            submitted = st.form_submit_button("Create Approval")

            if submitted:
                if title.strip():
                    add_approval(title, department, risk_level)
                    st.success("Approval request created.")
                    st.rerun()
                else:
                    st.error("Please enter approval title.")

        st.markdown("### Pending Approvals")

        approvals = get_memory_section("approvals")

        if approvals:
            for approval in approvals:
                data = approval.get("data", {})
                st.write(
                    f"**{data.get('title')}**  \n"
                    f"Department: {data.get('department')} | "
                    f"Risk: {data.get('risk_level')} | "
                    f"Status: {data.get('status')}  \n"
                    f"Created: {approval.get('created_at')}"
                )
                st.divider()
        else:
            st.info("No approval requests.")

    with tab4:
        st.markdown("### Add Daily Log")

        with st.form("add_daily_log_form"):
            summary = st.text_area("Daily Summary")

            submitted = st.form_submit_button("Save Log")

            if submitted:
                if summary.strip():
                    add_daily_log(summary)
                    st.success("Daily log saved.")
                    st.rerun()
                else:
                    st.error("Please enter a summary.")

        st.markdown("### Logs")

        logs = get_memory_section("daily_logs")

        if logs:
            for log in logs:
                data = log.get("data", {})
                st.write(
                    f"**{log.get('created_at')}**  \n"
                    f"{data.get('summary')}"
                )
                st.divider()
        else:
            st.info("No daily logs yet.")