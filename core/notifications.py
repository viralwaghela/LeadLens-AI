from core.memory import add_memory_entry, get_memory_section


def create_notification(title, message, department="System", level="Info"):
    return add_memory_entry("reports", {
        "type": "Notification",
        "title": title,
        "message": message,
        "department": department,
        "level": level,
        "status": "Unread"
    })


def get_notifications():
    reports = get_memory_section("reports")

    notifications = [
        report for report in reports
        if report.get("data", {}).get("type") == "Notification"
    ]

    return notifications