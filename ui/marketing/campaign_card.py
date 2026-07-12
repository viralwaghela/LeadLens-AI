import streamlit as st


def show_campaign_card(entry, index):
    data = entry.get("data", {})
    task = data.get("task", {})
    output = data.get("output", {})
    deliverables = data.get("deliverables", {})

    campaign_name = output.get("campaign_name", task.get("title", "Untitled Campaign"))
    created_at = entry.get("created_at", "")
    status = "Completed" if deliverables else "Generated"

    with st.container(border=True):
        col1, col2, col3 = st.columns([4, 2, 1])

        with col1:
            st.markdown(f"### {campaign_name}")
            st.caption(f"Created: {created_at}")
            st.write(f"Task: {task.get('title', 'N/A')}")

        with col2:
            st.success(status)

        with col3:
            if st.button("Open", key=f"open_campaign_{index}"):
                st.session_state.selected_campaign_index = index
                st.rerun()