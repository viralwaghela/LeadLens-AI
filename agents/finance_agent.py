from agents.router import register_agent

from core.memory import add_memory_entry
from core.activity import log_activity
from core.notifications import create_notification

from finance.engine import generate_finance_report
from finance.exporters import export_finance_deliverables


def finance_agent(task):
    log_activity(
        title=f"Finance task started: {task.get('title', '')}",
        department="Finance",
        activity_type="Started",
        details="Finance Department started generating financial reports."
    )

    report = generate_finance_report(task)

    export_result = None

    if "error" not in report:
        export_result = export_finance_deliverables(report)

    add_memory_entry(
        "finance",
        {
            "task": task,
            "output": report,
            "deliverables": export_result
        }
    )

    if "error" in report:
        create_notification(
            title="Finance Report Failed",
            message="Finance Department failed to generate report.",
            department="Finance",
            level="Error"
        )

        log_activity(
            title="Finance report failed",
            department="Finance",
            activity_type="Error",
            details="Finance Department returned invalid JSON."
        )

        return {
            "success": False,
            "department": "Finance",
            "task": task,
            "status": "Failed",
            "message": "Finance Department failed."
        }

    create_notification(
        title="Finance Report Completed",
        message=f"Generated report: {report.get('report_name', 'Finance Report')}",
        department="Finance",
        level="Success"
    )

    log_activity(
        title="Finance report completed",
        department="Finance",
        activity_type="Completed",
        details=f"Generated deliverables for {report.get('report_name', 'Finance Report')}"
    )

    return {
        "success": True,
        "department": "Finance",
        "task": task,
        "status": "Completed",
        "message": "Finance Department completed successfully."
    }


register_agent("Finance", finance_agent)