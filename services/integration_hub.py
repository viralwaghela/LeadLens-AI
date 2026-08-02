from __future__ import annotations

from datetime import datetime
from typing import Any

from core.memory import load_memory, save_memory, generate_id

SUPPORTED = {
    "Google Sheets": "Import live operational tables using a service account or OAuth credentials.",
    "Gmail": "Read lead conversations and create approval-based drafts.",
    "Google Calendar": "Read meetings and create approved events.",
    "Meta / Instagram": "Read insights and publish approved campaign content through the Graph API.",
    "WhatsApp Business": "Send approved templates and capture customer replies.",
    "CRM Webhook": "Receive lead and pipeline updates from external CRMs.",
}


def list_integrations() -> list[dict[str, Any]]:
    memory = load_memory()
    current = {x.get("data", {}).get("name"): x.get("data", {}) for x in memory.get("integration_connections", [])}
    return [{"name": name, "description": desc, "status": current.get(name, {}).get("status", "Not connected"), "configured_at": current.get(name, {}).get("configured_at", "")} for name, desc in SUPPORTED.items()]


def save_connection(name: str, config: dict[str, Any]) -> dict[str, Any]:
    if name not in SUPPORTED:
        raise ValueError("Unsupported integration")
    memory = load_memory()
    safe_config = {k: ("••••••••" if any(token in k.lower() for token in ["secret", "token", "password", "key"]) else v) for k, v in config.items()}
    entry = {"id": generate_id("integration_connections"), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "data": {"name": name, "status": "Configured", "configured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "config_summary": safe_config}}
    memory["integration_connections"] = [x for x in memory.get("integration_connections", []) if x.get("data", {}).get("name") != name]
    memory["integration_connections"].append(entry)
    save_memory(memory)
    return entry
