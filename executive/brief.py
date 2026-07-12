from datetime import datetime


DEPARTMENT_CONFIG = {
    "marketing": {
        "label": "Marketing",
        "title_keys": ("campaign_name", "package_name"),
    },
    "sales": {
        "label": "Sales",
        "title_keys": ("campaign_name", "package_name"),
    },
    "finance": {
        "label": "Finance",
        "title_keys": ("report_name", "package_name"),
    },
    "hr": {
        "label": "HR",
        "title_keys": ("package_name", "report_name"),
    },
    "operations": {
        "label": "Operations",
        "title_keys": ("package_name", "report_name"),
    },
}


def _parse_datetime(value):
    if not value:
        return None

    supported_formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    )

    for date_format in supported_formats:
        try:
            return datetime.strptime(value, date_format)
        except (TypeError, ValueError):
            continue

    return None


def _get_output_title(entry, title_keys):
    data = entry.get("data", {})
    output = data.get("output", {})
    task = data.get("task", {})

    for key in title_keys:
        value = output.get(key)

        if value:
            return str(value)

    return (
        task.get("title")
        or output.get("title")
        or "New output completed"
    )


def _get_department_status(entry):
    data = entry.get("data", {})
    output = data.get("output", {})
    deliverables = data.get("deliverables")

    if output.get("error"):
        return "Needs attention"

    if deliverables:
        return "Completed"

    return "Generated"


def _build_department_items(memory):
    items = []

    for section, config in DEPARTMENT_CONFIG.items():
        entries = memory.get(section, [])

        if not entries:
            continue

        latest = entries[-1]

        items.append(
            {
                "department": config["label"],
                "title": _get_output_title(
                    latest,
                    config["title_keys"],
                ),
                "status": _get_department_status(latest),
                "created_at": latest.get("created_at", ""),
                "timestamp": _parse_datetime(
                    latest.get("created_at")
                ),
            }
        )

    return items


def _build_approval_items(memory):
    items = []

    for approval in memory.get("approvals", []):
        data = approval.get("data", {})
        status = data.get("status", "Pending")

        if status not in ("Approved", "Rejected"):
            continue

        title = data.get("title", "Approval decision")
        department = data.get("department", "Executive")

        items.append(
            {
                "department": department,
                "title": f"{title} — {status.lower()}",
                "status": status,
                "created_at": (
                    approval.get("resolved_at")
                    or approval.get("created_at", "")
                ),
                "timestamp": _parse_datetime(
                    approval.get("resolved_at")
                    or approval.get("created_at")
                ),
            }
        )

    return items


def _build_activity_items(memory):
    items = []

    for activity in memory.get("activities", []):
        data = activity.get("data", {})

        title = data.get("title")

        if not title:
            continue

        items.append(
            {
                "department": data.get(
                    "department",
                    "Business",
                ),
                "title": title,
                "status": data.get(
                    "activity_type",
                    "Activity",
                ),
                "created_at": activity.get(
                    "created_at",
                    "",
                ),
                "timestamp": _parse_datetime(
                    activity.get("created_at")
                ),
            }
        )

    return items


def build_executive_brief(memory, limit=6):
    items = []

    items.extend(_build_department_items(memory))
    items.extend(_build_approval_items(memory))
    items.extend(_build_activity_items(memory))

    unique_items = []
    seen = set()

    for item in items:
        unique_key = (
            item.get("department", "").lower(),
            item.get("title", "").lower(),
            item.get("created_at", ""),
        )

        if unique_key in seen:
            continue

        seen.add(unique_key)
        unique_items.append(item)

    unique_items.sort(
        key=lambda item: (
            item.get("timestamp")
            or datetime.min
        ),
        reverse=True,
    )

    return unique_items[:limit]


def get_pending_approval_count(memory):
    return sum(
        1
        for approval in memory.get("approvals", [])
        if approval.get("data", {}).get("status") == "Pending"
    )


def build_brief_headline(memory):
    brief = build_executive_brief(memory)
    pending_count = get_pending_approval_count(memory)

    if not brief and pending_count == 0:
        return {
            "title": "Your business is ready for review.",
            "message": (
                "No recent department activity or pending "
                "decisions were found."
            ),
        }

    if pending_count == 0:
        decision_message = "No decisions require your attention."
    elif pending_count == 1:
        decision_message = "One decision requires your attention."
    else:
        decision_message = (
            f"{pending_count} decisions require your attention."
        )

    return {
        "title": "Your latest business update is ready.",
        "message": decision_message,
    }