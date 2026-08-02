from __future__ import annotations

from datetime import datetime
from typing import Any

from core.memory import load_memory, save_memory, generate_id

ALLOWED_ACTIONS = {"create_task", "activate_campaign", "draft_email", "schedule_content", "record_decision"}


def propose_action(action_type: str, payload: dict[str, Any], risk: str = "Medium") -> dict[str, Any]:
    if action_type not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported action: {action_type}")
    memory = load_memory()
    action = {
        "id": generate_id("automation_runs"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": {"action_type": action_type, "payload": payload, "risk": risk, "status": "Awaiting Approval"},
    }
    memory.setdefault("automation_runs", []).append(action)
    memory.setdefault("approvals", []).append({
        "id": generate_id("approvals"),
        "created_at": action["created_at"],
        "data": {"title": f"Execute: {action_type.replace('_', ' ').title()}", "department": payload.get("department", "Chief of Staff"), "risk_level": risk, "status": "Pending", "reference_id": action["id"]},
    })
    save_memory(memory)
    return action


def execute_action(action_id: str) -> dict[str, Any]:
    memory = load_memory()
    action = next((x for x in memory.get("automation_runs", []) if x.get("id") == action_id), None)
    if not action:
        raise ValueError("Action not found")
    data = action.setdefault("data", {})
    payload = data.get("payload", {})
    action_type = data.get("action_type")
    result: dict[str, Any] = {"message": "Action completed"}
    if action_type == "create_task":
        task = {"id": generate_id("tasks"), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "data": {"title": payload.get("title", "New task"), "department": payload.get("department", "Operations"), "priority": payload.get("priority", "Medium"), "status": "Pending", "notes": payload.get("notes", "")}}
        memory.setdefault("tasks", []).append(task)
        result = {"task_id": task["id"], "message": "Task created"}
    elif action_type == "activate_campaign":
        cid = payload.get("campaign_id")
        for campaign in memory.get("campaigns", []):
            if campaign.get("id") == cid:
                campaign.setdefault("data", {})["status"] = "Active"
                result = {"campaign_id": cid, "message": "Campaign activated"}
    elif action_type == "draft_email":
        result = {"subject": payload.get("subject", "LeadLens draft"), "body": payload.get("body", ""), "message": "Email draft prepared locally"}
    elif action_type == "schedule_content":
        result = {"scheduled_items": len(payload.get("items", [])), "message": "Content schedule recorded locally"}
    elif action_type == "record_decision":
        decision = {"id": generate_id("decisions"), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "data": payload}
        memory.setdefault("decisions", []).append(decision)
        result = {"decision_id": decision["id"], "message": "Decision recorded"}
    data["status"] = "Completed"
    data["result"] = result
    action["executed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_memory(memory)
    return result
