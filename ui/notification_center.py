import streamlit as st

from core.notifications import get_notifications


def show_notification_center():
    st.subheader("🔔 Notification Center")

    notifications = get_notifications()

    if not notifications:
        st.info("No notifications yet.")
        return

    unread_count = sum(
        1 for item in notifications
        if item.get("data", {}).get("status") == "Unread"
    )

    st.metric("Unread Notifications", unread_count)

    for notification in reversed(notifications[-20:]):
        data = notification.get("data", {})

        level = data.get("level", "Info")

        if level == "Success":
            st.success(f"{data.get('title', '')} — {data.get('message', '')}")
        elif level == "Warning":
            st.warning(f"{data.get('title', '')} — {data.get('message', '')}")
        elif level == "Error":
            st.error(f"{data.get('title', '')} — {data.get('message', '')}")
        else:
            st.info(f"{data.get('title', '')} — {data.get('message', '')}")

        st.caption(f"{data.get('department', 'System')} • {notification.get('created_at', '')}")