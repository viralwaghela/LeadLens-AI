import streamlit as st

from core.memory import load_memory
from ui.hr.employee_card import show_employee_card
from ui.hr.employee_details import show_employee_details


def show_hr_dashboard():

    st.subheader("👥 HR Department")

    memory = load_memory()

    hr_entries = memory.get("hr", [])

    if not hr_entries:
        st.info("No HR packages generated yet.")
        return

    if "selected_hr_package" not in st.session_state:
        st.session_state.selected_hr_package = None

    if st.session_state.selected_hr_package is None:

        st.markdown("### HR Package History")

        for index, entry in enumerate(reversed(hr_entries)):
            real_index = len(hr_entries) - 1 - index
            show_employee_card(entry, real_index)

    else:

        package = hr_entries[
            st.session_state.selected_hr_package
        ]

        if st.button("← Back to HR Packages"):
            st.session_state.selected_hr_package = None
            st.rerun()

        show_employee_details(package)