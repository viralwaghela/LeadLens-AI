import streamlit as st


def show_operations_card(entry, index):

    data = entry.get("data", {})

    task = data.get("task", {})

    output = data.get("output", {})

    deliverables = data.get("deliverables", {})

    package_name = output.get(
        "package_name",
        task.get("title", "Operations Package")
    )

    created_at = entry.get("created_at", "")

    status = "Completed" if deliverables else "Generated"

    with st.container(border=True):

        col1, col2, col3 = st.columns([4,2,1])

        with col1:

            st.markdown(f"### {package_name}")

            st.caption(created_at)

            st.write(task.get("title",""))

        with col2:

            st.success(status)

        with col3:

            if st.button(
                "Open",
                key=f"operations_{index}"
            ):
                st.session_state.selected_operations = index
                st.rerun()