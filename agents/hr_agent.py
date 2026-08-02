from agents.router import register_agent

from core.memory import add_memory_entry
from core.activity import log_activity
from core.notifications import create_notification

from hr.engine import generate_hr_package
from hr.exporters import export_hr_deliverables


def hr_agent(task):
    log_activity(
        title=f"HR task started: {task.get('title', '')}",
        department="HR",
        activity_type="Started",
        details="HR Department started generating hiring package."
    )

    package = generate_hr_package(task)

    export_result = None

    if "error" not in package:
        export_result = export_hr_deliverables(package)

    add_memory_entry(
        "hr",
        {
            "task": task,
            "output": package,
            "deliverables": export_result
        }
    )

    if "error" in package:
        create_notification(
            title="HR Package Failed",
            message="HR Department failed to generate hiring package.",
            department="HR",
            level="Error"
        )

        log_activity(
            title="HR package failed",
            department="HR",
            activity_type="Error",
            details="HR Department returned invalid JSON."
        )

        return {
            "success": False,
            "department": "HR",
            "task": task,
            "status": "Failed",
            "message": "HR Department failed."
        }

    create_notification(
        title="HR Package Completed",
        message=f"Generated hiring package: {package.get('package_name', 'HR Package')}",
        department="HR",
        level="Success"
    )

    log_activity(
        title="HR package completed",
        department="HR",
        activity_type="Completed",
        details=f"Generated deliverables for {package.get('package_name', 'HR Package')}"
    )

    return {
        "success": True,
        "department": "HR",
        "task": task,
        "status": "Completed",
        "message": "HR Department completed successfully."
    }


register_agent("HR", hr_agent)