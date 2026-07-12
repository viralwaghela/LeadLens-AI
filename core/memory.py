import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_FOLDER = PROJECT_ROOT / "database"
COMPANY_FILE = DATABASE_FOLDER / "company.json"

DEFAULT_MEMORY = {
    "company": {},
    "daily_logs": [],
    "decisions": [],
    "tasks": [],
    "completed_tasks": [],
    "meetings": [],
    "campaigns": [],
    "reports": [],
    "employees": [],
    "clients": [],
    "sales": [],
    "expenses": [],
    "marketing": [],
    "finance": [],
    "hr": [],
    "operations": [],
    "kpis": [],
    "approvals": [],
}


def _fresh_default():
    return copy.deepcopy(DEFAULT_MEMORY)


def ensure_database():
    DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)
    if not COMPANY_FILE.exists():
        save_memory(_fresh_default())


def load_memory():
    ensure_database()
    try:
        with COMPANY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("Invalid database format")
        if "company" not in data:
            old_company_data = data
            data = _fresh_default()
            data["company"] = old_company_data
        for key, value in DEFAULT_MEMORY.items():
            data.setdefault(key, copy.deepcopy(value))
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        backup = COMPANY_FILE.with_suffix(".corrupt.json")
        try:
            if COMPANY_FILE.exists():
                os.replace(COMPANY_FILE, backup)
        except OSError:
            pass
        data = _fresh_default()
        save_memory(data)
        return data


def save_memory(memory):
    DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(prefix="company_", suffix=".json", dir=DATABASE_FOLDER)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(memory, file, indent=4, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, COMPANY_FILE)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def generate_id(section):
    prefix = section.upper().replace("_", "-")
    return f"{prefix}-{uuid4().hex[:8].upper()}"


def company_exists():
    return bool(load_memory().get("company", {}))


def load_company():
    return load_memory().get("company", {})


def save_company(company_data):
    memory = load_memory()
    memory["company"] = company_data
    save_memory(memory)


def update_company(key, value):
    memory = load_memory()
    memory.setdefault("company", {})[key] = value
    save_memory(memory)


def reset_company():
    save_memory(_fresh_default())


def get_company_value(key, default=None):
    return load_company().get(key, default)


def add_memory_entry(section, entry):
    memory = load_memory()
    memory.setdefault(section, [])
    entry_data = {
        "id": generate_id(section),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": entry,
    }
    memory[section].append(entry_data)
    save_memory(memory)
    return entry_data


def get_memory_section(section):
    return load_memory().get(section, [])


def _open_entry_exists(section, title, department=""):
    title_key = title.strip().casefold()
    department_key = department.strip().casefold()
    for item in get_memory_section(section):
        data = item.get("data", {})
        same_title = str(data.get("title", "")).strip().casefold() == title_key
        same_department = not department_key or str(data.get("department", "")).strip().casefold() == department_key
        status = str(data.get("status", "Pending")).casefold()
        if same_title and same_department and status in {"pending", "open"}:
            return item
    return None


def add_task(title, department, priority="Medium", status="Pending"):
    if not title.strip():
        return None
    existing = _open_entry_exists("tasks", title, department)
    if existing:
        return existing
    return add_memory_entry("tasks", {
        "title": title,
        "department": department,
        "priority": priority,
        "status": status,
    })


def complete_task(task_id):
    memory = load_memory()
    remaining_tasks = []
    completed_task = None
    for task in memory.get("tasks", []):
        if task.get("id") == task_id:
            completed_task = task
            completed_task.setdefault("data", {})["status"] = "Completed"
            completed_task["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            remaining_tasks.append(task)
    if completed_task:
        memory["tasks"] = remaining_tasks
        memory.setdefault("completed_tasks", []).append(completed_task)
        save_memory(memory)
    return completed_task


def add_decision(title, reason, impact="Medium"):
    if not title.strip():
        return None
    return add_memory_entry("decisions", {"title": title, "reason": reason, "impact": impact})


def add_approval(title, department, risk_level="Medium", status="Pending"):
    if not title.strip():
        return None
    existing = _open_entry_exists("approvals", title, department)
    if existing:
        return existing
    return add_memory_entry("approvals", {
        "title": title,
        "department": department,
        "risk_level": risk_level,
        "status": status,
    })


def update_approval_status(approval_id, status):
    """Resolve an approval and any duplicate pending copies.

    Older LeadLens builds could create duplicate approval cards. Resolving only
    one copy made the same decision appear again immediately, which looked like
    the button had failed. This function resolves the selected approval and all
    pending duplicates with the same title and department in one atomic write.
    It also completes any pending task with the exact same title.
    """
    canonical_status = str(status).strip().title()
    allowed = {"Approved", "Rejected", "Pending"}
    if canonical_status not in allowed:
        raise ValueError("Invalid approval status")

    memory = load_memory()
    approvals = memory.get("approvals", [])
    selected = next(
        (item for item in approvals if item.get("id") == approval_id),
        None,
    )
    if selected is None:
        return None

    selected_data = selected.setdefault("data", {})
    title_key = str(selected_data.get("title", "")).strip().casefold()
    department_key = str(selected_data.get("department", "")).strip().casefold()
    resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resolved_ids = []

    for approval in approvals:
        data = approval.setdefault("data", {})
        same_title = str(data.get("title", "")).strip().casefold() == title_key
        same_department = (
            str(data.get("department", "")).strip().casefold()
            == department_key
        )
        is_pending = str(data.get("status", "Pending")).strip().casefold() in {
            "pending",
            "open",
        }
        if approval.get("id") == approval_id or (
            same_title and same_department and is_pending
        ):
            data["status"] = canonical_status
            approval["resolved_at"] = resolved_at
            resolved_ids.append(approval.get("id"))

    # Complete exact-title tasks when an approval represents the task itself.
    if canonical_status == "Approved":
        remaining_tasks = []
        for task in memory.get("tasks", []):
            task_data = task.setdefault("data", {})
            task_title = str(task_data.get("title", "")).strip().casefold()
            task_department = str(task_data.get("department", "")).strip().casefold()
            if task_title == title_key and task_department == department_key:
                task_data["status"] = "Completed"
                task["completed_at"] = resolved_at
                memory.setdefault("completed_tasks", []).append(task)
            else:
                remaining_tasks.append(task)
        memory["tasks"] = remaining_tasks

    action_word = "approved" if canonical_status == "Approved" else "rejected"
    memory.setdefault("reports", []).append({
        "id": generate_id("reports"),
        "created_at": resolved_at,
        "data": {
            "type": "Activity",
            "title": f"Decision {action_word}: {selected_data.get('title', 'Approval')}",
            "department": selected_data.get("department", "System"),
            "activity_type": canonical_status,
            "details": (
                f"Resolved {len(resolved_ids)} matching pending approval"
                f"{'s' if len(resolved_ids) != 1 else ''}."
            ),
        },
    })
    memory.setdefault("reports", []).append({
        "id": generate_id("reports"),
        "created_at": resolved_at,
        "data": {
            "type": "Notification",
            "title": f"Approval {canonical_status}",
            "message": selected_data.get("title", "Approval decision updated"),
            "department": selected_data.get("department", "System"),
            "level": "Success" if canonical_status == "Approved" else "Warning",
            "status": "Unread",
        },
    })

    save_memory(memory)
    return {
        "approval": selected,
        "resolved_ids": resolved_ids,
        "status": canonical_status,
    }


def add_daily_log(summary):
    if not str(summary).strip():
        return None
    return add_memory_entry("daily_logs", {"summary": summary})
