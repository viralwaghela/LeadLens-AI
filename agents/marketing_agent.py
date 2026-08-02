from agents.router import register_agent
from core.memory import add_memory_entry
from core.activity import log_activity
from core.notifications import create_notification
from marketing.engine import generate_marketing_campaign
from marketing.exporters import export_marketing_deliverables


def marketing_agent(task):
    log_activity(
        title=f"Marketing task started: {task.get('title', '')}",
        department="Marketing",
        activity_type="Started",
        details="Marketing Department started generating campaign assets."
    )

    campaign = generate_marketing_campaign(task)

    export_result = None

    if "error" not in campaign:
        export_result = export_marketing_deliverables(campaign)

    add_memory_entry("marketing", {
        "task": task,
        "output": campaign,
        "deliverables": export_result
    })

    if "error" in campaign:
        create_notification(
            title="Marketing Campaign Failed",
            message="Marketing Agent failed to generate a valid campaign.",
            department="Marketing",
            level="Error"
        )

        log_activity(
            title="Marketing task failed",
            department="Marketing",
            activity_type="Error",
            details="Marketing Agent returned invalid campaign output."
        )

        return {
            "success": False,
            "department": "Marketing",
            "task": task,
            "status": "Failed",
            "message": "Marketing Agent failed to generate valid campaign output."
        }

    create_notification(
        title="Marketing Campaign Completed",
        message=f"Campaign deliverables generated for: {task.get('title', '')}",
        department="Marketing",
        level="Success"
    )

    log_activity(
        title="Marketing campaign deliverables generated",
        department="Marketing",
        activity_type="Completed",
        details=f"Generated documents for task: {task.get('title', '')}"
    )

    return {
        "success": True,
        "department": "Marketing",
        "task": task,
        "status": "Completed",
        "message": f"Marketing Agent generated campaign deliverables for: {task.get('title', '')}"
    }


register_agent("Marketing", marketing_agent)