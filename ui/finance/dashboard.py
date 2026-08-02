import streamlit as st

from core.memory import load_memory
from ui.finance.report_card import show_report_card
from ui.finance.report_details import show_report_details


def show_finance_dashboard():
    st.subheader("💰 Finance Department")

    memory = load_memory()
    finance_entries = memory.get("finance", [])

    if not finance_entries:
        st.info("No finance reports generated yet.")
        return

    if "selected_finance_report" not in st.session_state:
        st.session_state.selected_finance_report = None

    if st.session_state.selected_finance_report is None:
        st.markdown("### Finance Report History")

        for index, entry in enumerate(reversed(finance_entries)):
            real_index = len(finance_entries) - 1 - index
            show_report_card(entry, real_index)

    else:
        report = finance_entries[st.session_state.selected_finance_report]

        if st.button("← Back to Finance Reports"):
            st.session_state.selected_finance_report = None
            st.rerun()

        show_report_details(report)