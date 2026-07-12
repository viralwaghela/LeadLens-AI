from agents.router import register_agent

from core.memory import add_memory_entry
from core.activity import log_activity
from core.notifications import create_notification

from operations.engine import generate_operations_package
from operations.exporters import export_operations_deliverables


def operations_agent(task):
    log_activity(
        title=f"Operations task started: {task.get('title', '')}",
        department="Operations",
        activity_type="Started",
        details="Operations Department started generating execution package."
    )

    package = generate_operations_package(task)

    export_result = None

    if "error" not in package:
        export_result = export_operations_deliverables(package)

    add_memory_entry(
        "operations",
        {
            "task": task,
            "output": package,
            "deliverables": export_result
        }
    )

    if "error" in package:
        create_notification(
            title="Operations Package Failed",
            message="Operations Department failed to generate execution package.",
            department="Operations",
            level="Error"
        )

        log_activity(
            title="Operations package failed",
            department="Operations",
            activity_type="Error",
            details="Operations Department returned invalid JSON."
        )

        return {
            "success": False,
            "department": "Operations",
            "task": task,
            "status": "Failed",
            "message": "Operations Department failed."
        }

    create_notification(
        title="Operations Package Completed",
        message=f"Generated operations package: {package.get('package_name', 'Operations Package')}",
        department="Operations",
        level="Success"
    )

    log_activity(
        title="Operations package completed",
        department="Operations",
        activity_type="Completed",
        details=f"Generated deliverables for {package.get('package_name', 'Operations Package')}"
    )

    return {
        "success": True,
        "department": "Operations",
        "task": task,
        "status": "Completed",
        "message": "Operations Department completed successfully."
    }


register_agent("Operations", operations_agent)