import streamlit as st

from core.memory import get_memory_section
from ui.sales.lead_card import show_lead_card
from ui.sales.lead_details import show_lead_details


def show_sales_dashboard():
    st.subheader("💼 Sales Department")

    sales_entries = get_memory_section("sales")

    if not sales_entries:
        st.info("No sales campaigns generated yet.")
        return

    if "selected_sales_campaign" not in st.session_state:
        st.session_state.selected_sales_campaign = None

    if st.session_state.selected_sales_campaign is None:

        st.markdown("### Sales Campaign History")

        for index, entry in enumerate(reversed(sales_entries)):
            real_index = len(sales_entries) - 1 - index
            show_lead_card(entry, real_index)

    else:

        campaign = sales_entries[
            st.session_state.selected_sales_campaign
        ]

        if st.button("← Back to Campaign History"):
            st.session_state.selected_sales_campaign = None
            st.rerun()

        show_lead_details(campaign)