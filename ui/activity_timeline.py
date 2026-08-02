import streamlit as st

from core.activity import get_activity_timeline


def show_activity_timeline():
    st.subheader("🕒 Activity Timeline")

    activities = get_activity_timeline()

    if not activities:
        st.info("No activity yet.")
        return

    for activity in reversed(activities[-20:]):
        data = activity.get("data", {})

        st.write(
            f"**{activity.get('created_at', '')}**  \n"
            f"{data.get('department', 'System')} • {data.get('activity_type', 'Info')}  \n"
            f"**{data.get('title', '')}**"
        )

        if data.get("details"):
            st.caption(data.get("details"))

        st.divider()