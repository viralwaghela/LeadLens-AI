from __future__ import annotations

from datetime import datetime
from typing import Any

from core.memory import load_memory, save_memory, generate_id

SECTIONS = {
    "strategic": "strategic_memory",
    "preference": "preferences",
    "relationship": "relationships",
    "lesson": "lessons",
    "risk": "risks",
}


def ensure_memory_schema() -> dict[str, Any]:
    memory = load_memory()
    for key in [*SECTIONS.values(), "automation_runs", "monitoring_events", "integration_connections", "content_assets"]:
        memory.setdefault(key, [])
    memory.setdefault("settings", {"autonomy_level": "approval_required", "daily_brief_enabled": True})
    save_memory(memory)
    return memory


def remember(kind: str, text: str, tags: list[str] | None = None, importance: str = "Medium", source: str = "Founder") -> dict[str, Any]:
    clean = str(text or "").strip()
    if not clean:
        raise ValueError("Memory text is required")
    memory = ensure_memory_schema()
    section = SECTIONS.get(kind, kind)
    memory.setdefault(section, [])
    entry = {
        "id": generate_id(section),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": {
            "text": clean,
            "tags": tags or [],
            "importance": importance,
            "source": source,
            "active": True,
        },
    }
    memory[section].append(entry)
    save_memory(memory)
    return entry


def compact_context(max_items: int = 6) -> dict[str, Any]:
    memory = ensure_memory_schema()
    company = memory.get("company", {})
    context: dict[str, Any] = {
        "company": company,
        "goals": company.get("goals", []),
        "services": company.get("services", []),
        "monthly_revenue": company.get("monthly_revenue", 0),
        "monthly_expenses": company.get("monthly_expenses", 0),
    }
    for name, section in SECTIONS.items():
        items = [x for x in memory.get(section, []) if x.get("data", {}).get("active", True)]
        context[name] = [x.get("data", {}).get("text", "") for x in items[-max_items:]]
    context["recent_decisions"] = [x.get("data", {}) for x in memory.get("decisions", [])[-max_items:]]
    context["recent_campaigns"] = [x.get("data", {}) for x in memory.get("campaigns", [])[-max_items:]]
    return context


def deactivate_memory(section: str, entry_id: str) -> bool:
    memory = ensure_memory_schema()
    changed = False
    for item in memory.get(section, []):
        if item.get("id") == entry_id:
            item.setdefault("data", {})["active"] = False
            changed = True
    if changed:
        save_memory(memory)
    return changed
