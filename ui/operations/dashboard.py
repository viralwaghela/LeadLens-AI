import streamlit as st

from core.memory import load_memory
from ui.operations.operations_card import show_operations_card
from ui.operations.operations_details import show_operations_details


def show_operations_dashboard():

    st.subheader("⚙️ Operations Department")

    memory = load_memory()

    operations = memory.get("operations", [])

    if not operations:
        st.info("No operations packages generated yet.")
        return

    if "selected_operations" not in st.session_state:
        st.session_state.selected_operations = None

    if st.session_state.selected_operations is None:

        st.markdown("### Operations History")

        for index, entry in enumerate(reversed(operations)):
            real_index = len(operations) - 1 - index
            show_operations_card(entry, real_index)

    else:

        package = operations[
            st.session_state.selected_operations
        ]

        if st.button("← Back to Operations History"):
            st.session_state.selected_operations = None
            st.rerun()

        show_operations_details(package)