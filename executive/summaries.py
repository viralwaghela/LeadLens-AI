def get_entry_title(entry, fallback="No data"):
    if not entry:
        return fallback

    data = entry.get("data", {})
    output = data.get("output", {})
    task = data.get("task", {})

    return (
        output.get("campaign_name")
        or output.get("report_name")
        or output.get("package_name")
        or task.get("title")
        or fallback
    )


def get_entry_date(entry):
    if not entry:
        return "N/A"

    return entry.get("created_at", "N/A")


def build_department_summary(metrics):
    return {
        "Marketing": {
            "count": metrics["department_counts"].get("marketing", 0),
            "latest": get_entry_title(metrics.get("latest_marketing")),
            "date": get_entry_date(metrics.get("latest_marketing")),
        },
        "Sales": {
            "count": metrics["department_counts"].get("sales", 0),
            "latest": get_entry_title(metrics.get("latest_sales")),
            "date": get_entry_date(metrics.get("latest_sales")),
        },
        "Finance": {
            "count": metrics["department_counts"].get("finance", 0),
            "latest": get_entry_title(metrics.get("latest_finance")),
            "date": get_entry_date(metrics.get("latest_finance")),
        },
        "HR": {
            "count": metrics["department_counts"].get("hr", 0),
            "latest": get_entry_title(metrics.get("latest_hr")),
            "date": get_entry_date(metrics.get("latest_hr")),
        },
        "Operations": {
            "count": metrics["department_counts"].get("operations", 0),
            "latest": get_entry_title(metrics.get("latest_operations")),
            "date": get_entry_date(metrics.get("latest_operations")),
        },
    }