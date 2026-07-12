import streamlit as st


def show_report_card(entry, index):
    data = entry.get("data", {})
    task = data.get("task", {})
    output = data.get("output", {})
    deliverables = data.get("deliverables", {})

    report_name = output.get(
        "report_name",
        task.get("title", "Untitled Finance Report")
    )

    created_at = entry.get("created_at", "")
    status = "Completed" if deliverables else "Generated"

    with st.container(border=True):
        col1, col2, col3 = st.columns([4, 2, 1])

        with col1:
            st.markdown(f"### {report_name}")
            st.caption(f"Created: {created_at}")
            st.write(f"Task: {task.get('title', 'N/A')}")

        with col2:
            st.success(status)

        with col3:
            if st.button("Open", key=f"finance_report_{index}"):
                st.session_state.selected_finance_report = index
                st.rerun()