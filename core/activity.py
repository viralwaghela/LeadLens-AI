from core.memory import add_memory_entry, get_memory_section


def log_activity(title, department="System", activity_type="Info", details=""):
    return add_memory_entry("reports", {
        "type": "Activity",
        "title": title,
        "department": department,
        "activity_type": activity_type,
        "details": details
    })


def get_activity_timeline():
    reports = get_memory_section("reports")

    activities = [
        report for report in reports
        if report.get("data", {}).get("type") == "Activity"
    ]

    return activities