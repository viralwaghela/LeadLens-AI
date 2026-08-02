from __future__ import annotations

from datetime import datetime
from typing import Any

from core.memory import load_memory, save_memory, generate_id


def run_monitors(persist: bool = True) -> list[dict[str, Any]]:
    memory = load_memory()
    company = memory.get("company", {})
    revenue = float(company.get("monthly_revenue", 0) or 0)
    expenses = float(company.get("monthly_expenses", 0) or 0)
    margin = ((revenue - expenses) / revenue * 100) if revenue else 0
    alerts: list[dict[str, Any]] = []
    if revenue and expenses > revenue:
        alerts.append({"severity": "Critical", "area": "Finance", "message": "Monthly expenses exceed revenue. Freeze non-essential spend and review cash flow."})
    elif revenue and margin < 15:
        alerts.append({"severity": "High", "area": "Finance", "message": f"Operating margin is only {margin:.1f}%. Review pricing and variable costs."})
    open_tasks = [x for x in memory.get("tasks", []) if str(x.get("data", {}).get("status", "Pending")).lower() in {"pending", "open"}]
    if len(open_tasks) >= 8:
        alerts.append({"severity": "High", "area": "Operations", "message": f"Task backlog has reached {len(open_tasks)} items. Reprioritize and assign owners."})
    approvals = [x for x in memory.get("approvals", []) if str(x.get("data", {}).get("status", "Pending")).lower() == "pending"]
    if approvals:
        alerts.append({"severity": "Medium", "area": "Governance", "message": f"{len(approvals)} action(s) are waiting for founder approval."})
    campaigns = [x for x in memory.get("campaigns", []) if x.get("data", {}).get("status") in {"Draft", "Awaiting Approval", "Active"}]
    if campaigns and not approvals:
        alerts.append({"severity": "Low", "area": "Marketing", "message": "A campaign is ready for execution or performance review."})
    if not alerts:
        alerts.append({"severity": "Low", "area": "Business", "message": "No critical rule-based exceptions detected."})
    if persist:
        event = {"id": generate_id("monitoring_events"), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "data": {"alerts": alerts}}
        memory.setdefault("monitoring_events", []).append(event)
        save_memory(memory)
    return alerts
