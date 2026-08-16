from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from core.memory import company_exists, load_memory, update_memory

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "database" / "uploads"

# Phase 8: organization-scoped clinic/company settings, backed by
# core/db/models/organization.py's OrganizationSettings table instead of
# core/memory.py's single global `company` dict — the fix for true
# multi-organization onboarding (a second organization cannot share one
# global settings object). Defaults OFF, its own independent kill switch
# rather than being silently implied by LEADLENS_V2_AUTH_ENABLED, matching
# every other V2 mechanism's pattern. Only takes effect when a live
# organization actually resolves (core.identity.live_organization) — with
# it off, or with no resolvable organization, behavior is byte-identical
# to before this phase. See docs/V2_PHASE8_SAAS_ONBOARDING.md.
ORG_SCOPED_SETTINGS_ENABLED = os.getenv(
    "LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED", ""
).strip().lower() in {"1", "true", "yes"}

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from core.db.session import make_engine

        _ENGINE = make_engine()
    return _ENGINE


def _resolve_org_scoped_organization_id() -> int | None:
    """Returns an organization id only when org-scoped settings are on
    AND a real, live organization resolves (i.e. an authenticated V2
    session exists) — never the transitional default, since falling back
    to the default organization here would silently point a brand-new
    organization's onboarding at the SAME settings row every other
    unauthenticated/legacy caller uses, defeating the entire point of
    this mechanism. Returns None (meaning: use the legacy global path)
    for scripts, the scheduler, and any call with no live session."""
    if not ORG_SCOPED_SETTINGS_ENABLED:
        return None
    try:
        from core.auth import current_authenticated_session

        stored = current_authenticated_session()
    except Exception:  # noqa: BLE001 - no live Streamlit runtime
        stored = None
    return stored.organization_id if stored is not None else None


def money(value: Any) -> str:
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "₹0"


def _org_scoped_company() -> dict[str, Any] | None:
    """Returns the org-scoped company profile dict when applicable, else
    None (meaning: caller should fall back to the legacy global dict)."""
    org_id = _resolve_org_scoped_organization_id()
    if org_id is None:
        return None
    from core.db.session import session_scope
    from core.identity.organization_profile_service import get_settings

    with session_scope(_get_engine()) as session:
        return get_settings(session, org_id)


def business_snapshot() -> dict[str, Any]:
    memory = load_memory()
    company = _org_scoped_company()
    if company is None:
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
    from services.authorization_guard import require_permission

    require_permission("organization.manage")

    org_id = _resolve_org_scoped_organization_id()
    if org_id is not None:
        from core.db.session import session_scope
        from core.identity.organization_profile_service import save_settings

        with session_scope(_get_engine()) as session:
            save_settings(session, org_id, profile)
        return

    def mutate(memory):
        memory["company"] = profile
    update_memory(mutate)


def company_setup_complete() -> bool:
    """Phase 8: the organization-scoped replacement for
    core.memory.company_exists() app.py's onboarding routing should use.
    When org-scoped settings are on and a live organization resolves,
    checks THAT organization's own OrganizationSettings row — so a
    second, freshly-provisioned organization with no settings yet is
    correctly routed to onboarding even though some OTHER organization
    (or the legacy global company dict) already has one. Falls back to
    the legacy global check for scripts, the scheduler, and any call
    with no live session — unchanged single-clinic behavior."""
    org_id = _resolve_org_scoped_organization_id()
    if org_id is not None:
        from core.db.session import session_scope
        from core.identity.organization_profile_service import settings_exist

        with session_scope(_get_engine()) as session:
            return settings_exist(session, org_id)
    return company_exists()


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
