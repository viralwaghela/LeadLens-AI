from __future__ import annotations
import streamlit as st
from services.integration_manager_v21 import (
    decide_item,
    execute_item,
    execution_rows,
)
from services.platform_data import add_decision, add_task, business_snapshot, update_task


def show_action_center() -> None:
    st.markdown('<div class="eyebrow">EXECUTION LAYER</div>', unsafe_allow_html=True)
    st.markdown("## Action center")
    st.caption("Turn AI recommendations and management decisions into owned, trackable work.")
    snapshot = business_snapshot()
    tasks = snapshot["open_tasks"]
    approvals = [
        item
        for item in snapshot["pending_approvals"]
        if not item.get("data", {}).get("execution_id")
    ]
    prepared_actions = execution_rows()
    a, b, c, d = st.columns(4)
    a.metric("Open tasks", len(tasks))
    b.metric("Pending approvals", len(approvals))
    c.metric("Completed tasks", len(snapshot["memory"].get("completed_tasks", [])))
    d.metric(
        "Prepared actions",
        sum(
            row.get("status") not in {"Sent", "Simulated", "Failed", "Rejected"}
            for row in prepared_actions
        ),
    )

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Tasks", "Decisions", "Approvals", "Prepared actions"]
    )
    with tab1:
        with st.expander("Create task", expanded=False):
            with st.form("create_task"):
                title = st.text_input("Task")
                department = st.selectbox("Department", ["Executive", "Sales", "Marketing", "Finance", "HR", "Operations"])
                priority = st.selectbox("Priority", ["High", "Medium", "Low"])
                notes = st.text_area("Notes")
                if st.form_submit_button("Create task", type="primary"):
                    if title.strip():
                        add_task(title.strip(), department, priority, notes.strip())
                        st.success("Task created.")
                        st.rerun()
                    st.warning("Enter a task title.")
        if not tasks:
            st.info("No open tasks.")
        for item in tasks:
            data = item.get("data", {})
            with st.container(border=True):
                left, right = st.columns([5, 1])
                left.markdown(f"**{data.get('title', 'Untitled task')}**")
                left.caption(f"{data.get('department', 'General')} · {data.get('priority', 'Medium')} priority")
                if data.get("notes"):
                    left.write(data["notes"])
                if right.button("Complete", key=f"complete_{item.get('id')}", use_container_width=True):
                    update_task(item.get("id", ""), "Completed")
                    st.rerun()
    with tab2:
        with st.form("decision_form"):
            title = st.text_input("Decision title")
            reason = st.text_area("Reason and context")
            impact = st.selectbox("Impact", ["High", "Medium", "Low"])
            if st.form_submit_button("Record decision", type="primary"):
                if title.strip():
                    add_decision(title.strip(), reason.strip(), impact)
                    st.success("Decision recorded.")
                    st.rerun()
                st.warning("Enter a decision title.")
        for item in reversed(snapshot["memory"].get("decisions", [])[-10:]):
            data = item.get("data", {})
            with st.container(border=True):
                st.markdown(f"**{data.get('title', 'Decision')}**")
                st.caption(f"Impact: {data.get('impact', 'Medium')} · {item.get('created_at', '')}")
                st.write(data.get("reason", ""))
    with tab3:
        if not approvals:
            st.success("No decisions are waiting for approval.")
        for item in approvals:
            data = item.get("data", {})
            with st.container(border=True):
                st.markdown(f"**{data.get('title', 'Approval')}**")
                st.caption(f"{data.get('department', 'General')} · {data.get('risk_level', 'Medium')} risk")
                st.info("Resolve this approval from the Home dashboard to preserve the existing approval workflow.")
    with tab4:
        st.caption(
            "Every external action follows two explicit gates: approve the "
            "stored payload, then click Execute. Rejected actions cannot run."
        )
        if not prepared_actions:
            st.info("No external actions have been prepared.")
        for row in prepared_actions:
            with st.container(border=True):
                st.markdown(
                    f"**{row.get('title', 'Prepared action')}** "
                    f"· `{row.get('id', '')}`"
                )
                st.caption(
                    f"{row.get('provider', '').title()} · "
                    f"{row.get('action', '').replace('_', ' ')} · "
                    f"Status: {row.get('status', 'Unknown')}"
                )
                if row.get("impact"):
                    st.write(f"**Expected impact:** {row['impact']}")
                if row.get("recommendation_id"):
                    st.caption(
                        "Linked recommendation: "
                        + row["recommendation_id"]
                    )
                st.markdown("**Exact payload**")
                st.json(row.get("payload", {}))

                approval = str(row.get("approval_status", "Pending"))
                status = str(row.get("status", "Awaiting approval"))
                if approval.casefold() == "pending":
                    approve, reject = st.columns(2)
                    if approve.button(
                        "Approve payload",
                        key=f"prepared_approve_{row['id']}",
                        type="primary",
                        use_container_width=True,
                    ):
                        decide_item(row["id"], "Approved")
                        st.rerun()
                    if reject.button(
                        "Reject",
                        key=f"prepared_reject_{row['id']}",
                        use_container_width=True,
                    ):
                        decide_item(row["id"], "Rejected")
                        st.rerun()
                elif approval.casefold() == "approved" and status == "Approved":
                    st.warning(
                        "Approved, but not executed. Review the payload once "
                        "more before the final action."
                    )
                    if st.button(
                        "Execute approved action",
                        key=f"prepared_execute_{row['id']}",
                        type="primary",
                    ):
                        result = execute_item(row["id"])
                        if result.get("success"):
                            st.success(result.get("detail") or "Action completed.")
                        else:
                            st.error(result.get("detail") or "Action failed.")
                        st.rerun()
                elif status == "Rejected":
                    st.error("Rejected. This action is permanently blocked.")
                if row.get("result"):
                    st.markdown("**Verified execution result**")
                    st.json(row["result"])
