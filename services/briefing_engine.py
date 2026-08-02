from __future__ import annotations

from datetime import datetime
from typing import Any

from core.memory import load_memory
from services.monitoring_engine import run_monitors


def generate_morning_brief() -> dict[str, Any]:
    memory = load_memory()
    company = memory.get("company", {})
    revenue = float(company.get("monthly_revenue", 0) or 0)
    expenses = float(company.get("monthly_expenses", 0) or 0)
    profit = revenue - expenses
    open_tasks = [x for x in memory.get("tasks", []) if str(x.get("data", {}).get("status", "Pending")).lower() in {"pending", "open"}]
    approvals = [x for x in memory.get("approvals", []) if str(x.get("data", {}).get("status", "Pending")).lower() == "pending"]
    alerts = run_monitors(persist=False)
    priorities = []
    if approvals:
        priorities.append(f"Resolve {len(approvals)} pending approval(s).")
    if open_tasks:
        priorities.append(f"Review {len(open_tasks)} open task(s) and unblock the highest-priority owner.")
    priorities.extend([a["message"] for a in alerts[:2]])
    if not priorities:
        priorities.append("No critical exceptions detected. Focus on the highest-value growth initiative.")
    return {
        "date": datetime.now().strftime("%A, %d %B %Y"),
        "company": company.get("business_name", "Your business"),
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "margin": (profit / revenue * 100) if revenue else 0,
        "open_tasks": len(open_tasks),
        "pending_approvals": len(approvals),
        "alerts": alerts,
        "priorities": priorities[:4],
    }
