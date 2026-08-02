from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.memory import load_memory, update_memory

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "database" / "uploads"


def money(value: Any) -> str:
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "₹0"


def business_snapshot() -> dict[str, Any]:
    memory = load_memory()
    company = memory.get("company", {})
    revenue = float(company.get("monthly_revenue", 0) or 0)
    expenses = float(company.get("monthly_expenses", 0) or 0)
    profit = revenue - expenses
    margin = (profit / revenue * 100) if revenue else 0
    open_tasks = [x for x in memory.get("tasks", []) if str(x.get("data", {}).get("status", "Pending")).lower() in {"pending", "open"}]
    pending_approvals = [x for x in memory.get("approvals", []) if str(x.get("data", {}).get("status", "Pending")).lower() == "pending"]
    return {
        "company": company,
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "margin": margin,
        "open_tasks": open_tasks,
        "pending_approvals": pending_approvals,
        "memory": memory,
    }


def add_task(title: str, department: str, priority: str, notes: str = "") -> None:
    def mutate(memory):
        memory.setdefault("tasks", []).append({
            "id": f"TASK-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": {"title": title, "department": department, "priority": priority, "status": "Pending", "notes": notes},
        })
    update_memory(mutate)


def update_task(task_id: str, status: str) -> None:
    def mutate(memory):
        remaining = []
        for item in memory.get("tasks", []):
            if item.get("id") == task_id:
                item.setdefault("data", {})["status"] = status
                item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if status == "Completed":
                    memory.setdefault("completed_tasks", []).append(item)
                    continue
            remaining.append(item)
        memory["tasks"] = remaining
    update_memory(mutate)


def add_decision(title: str, reason: str, impact: str) -> None:
    def mutate(memory):
        memory.setdefault("decisions", []).append({
            "id": f"DEC-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": {"title": title, "reason": reason, "impact": impact},
        })
    update_memory(mutate)


def save_company_profile(profile: dict[str, Any]) -> None:
    def mutate(memory):
        memory["company"] = profile
    update_memory(mutate)


def save_uploaded_file(name: str, data: bytes) -> Path:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe = Path(name).name
    path = UPLOADS / safe
    path.write_bytes(data)
    return path


def preview_uploaded_file(name: str, data: bytes) -> tuple[list[dict[str, Any]], str]:
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        payload = json.loads(data.decode("utf-8"))
        if isinstance(payload, list):
            return payload[:50], f"{len(payload)} JSON records"
        return [{"key": k, "value": v} for k, v in payload.items()][:50], "JSON object"
    if suffix == ".csv":
        text = data.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        return rows[:50], f"{len(rows)} CSV rows"
    if suffix in {".txt", ".md"}:
        text = data.decode("utf-8", errors="replace")
        return [{"content": line} for line in text.splitlines()[:50]], f"{len(text)} characters"
    return [], "File stored. Preview is available for CSV, JSON, TXT, and MD files."
