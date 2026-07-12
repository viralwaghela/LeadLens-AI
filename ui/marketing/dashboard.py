import streamlit as st

from core.memory import get_memory_section
from ui.marketing.campaign_card import show_campaign_card
from ui.marketing.campaign_details import show_campaign_details


def show_marketing_dashboard():
    st.subheader("📢 Marketing Department")

    marketing_entries = get_memory_section("marketing")

    if not marketing_entries:
        st.info("No marketing campaigns generated yet.")
        return

    if "selected_campaign_index" not in st.session_state:
        st.session_state.selected_campaign_index = None

    if st.session_state.selected_campaign_index is None:
        st.markdown("### Campaign History")

        for index, entry in enumerate(reversed(marketing_entries)):
            real_index = len(marketing_entries) - 1 - index
            show_campaign_card(entry, real_index)

    else:
        selected_entry = marketing_entries[st.session_state.selected_campaign_index]

        if st.button("← Back to Campaign History"):
            st.session_state.selected_campaign_index = None
            st.rerun()

        show_campaign_details(selected_entry)