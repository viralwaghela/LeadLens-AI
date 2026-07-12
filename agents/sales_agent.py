from agents.router import register_agent

from core.memory import add_memory_entry
from core.activity import log_activity
from core.notifications import create_notification

from sales.engine import generate_sales_campaign
from sales.exporters import export_sales_deliverables


def sales_agent(task):
    log_activity(
        title=f"Sales task started: {task.get('title', '')}",
        department="Sales",
        activity_type="Started",
        details="Sales Department started generating sales campaign."
    )

    campaign = generate_sales_campaign(task)

    export_result = None

    if "error" not in campaign:
        export_result = export_sales_deliverables(campaign)

    add_memory_entry(
        "sales",
        {
            "task": task,
            "output": campaign,
            "deliverables": export_result
        }
    )

    if "error" in campaign:
        create_notification(
            title="Sales Campaign Failed",
            message="Sales Department failed to generate campaign.",
            department="Sales",
            level="Error"
        )

        log_activity(
            title="Sales campaign failed",
            department="Sales",
            activity_type="Error",
            details="Sales Department returned invalid JSON."
        )

        return {
            "success": False,
            "department": "Sales",
            "task": task,
            "status": "Failed",
            "message": "Sales Department failed."
        }

    create_notification(
        title="Sales Campaign Completed",
        message=f"Generated sales campaign: {campaign.get('campaign_name','Untitled')}",
        department="Sales",
        level="Success"
    )

    log_activity(
        title="Sales campaign completed",
        department="Sales",
        activity_type="Completed",
        details=f"Generated deliverables for {campaign.get('campaign_name','Untitled')}"
    )

    return {
        "success": True,
        "department": "Sales",
        "task": task,
        "status": "Completed",
        "message": "Sales Department completed successfully."
    }


register_agent("Sales", sales_agent)