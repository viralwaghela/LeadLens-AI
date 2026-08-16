"""Approval-gated preparation and execution of external actions.

Every action is validated and persisted before an approval is created. Nothing
is sent merely because Jarvis recommended it. An approved action still requires
an explicit Execute click, and every result is linked back to Jarvis memory.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from core.memory import (
    add_memory_entry,
    load_memory,
    update_approval_status,
    update_memory,
)
from integrations.calendar_service import GoogleCalendarService
from integrations.gmail_service import GmailService
from integrations.whatsapp_service import WhatsAppBusinessService
from services.jarvis_memory import record_action_execution
from services.security_service import audit_event


def _current_tenant_context():
    """Phase 6: resolve the same trusted transitional TenantContext every
    other Phase 5 hook point uses, so integration credential resolution
    is organization-scoped rather than reading os.environ directly.
    Returns None on any failure (DB unavailable, etc.) — callers must
    treat that exactly like "no tenant context available" and construct
    adapters with no credentials override, which reproduces pre-Phase-6
    behavior (adapter reads its own env vars) rather than breaking a
    live approval/execution flow."""
    try:
        from core.db.session import session_scope
        from core.identity.tenant_context import build_transitional_context
        from services.integration_credentials import _get_engine

        # Reuses the exact same (cached) engine services/integration_credentials.py
        # resolves credentials against, rather than an independently
        # constructed one — keeps org resolution and credential lookup
        # pointed at the same database always (in production both read
        # the same DATABASE_URL anyway; this also makes both resolvable
        # through one single test monkeypatch point).
        engine = _get_engine()
        with session_scope(engine) as session:
            return build_transitional_context(session)
    except Exception:  # noqa: BLE001 - must never break approval/execution
        return None


def _resolve_item_execution_context(item: dict[str, Any]):
    """Phase 6.1 — execute_item()'s trust boundary. Derives the operating
    TenantContext strictly from the queue item's own stamped
    `organization_id` (set by prepare_execution() at creation time) —
    never the transitional default, never a caller-supplied value,
    never a fallback of any kind. A missing, malformed, nonexistent, or
    inactive organization fails closed (returns None); the caller must
    refuse to execute rather than substitute any other organization's
    context. This is the fix for the Phase 6 audit finding that
    execute_item() previously resolved the transitional/default context
    unconditionally instead of deriving it from the item."""
    org_id = item.get("organization_id")
    if not isinstance(org_id, int) or isinstance(org_id, bool) or org_id <= 0:
        return None
    try:
        from core.db.models.organization import OrganizationStatus
        from core.db.session import session_scope
        from core.identity.organization_service import get_organization
        from core.identity.tenant_context import ActorType, build_system_context
        from services.integration_credentials import _get_engine

        engine = _get_engine()
        with session_scope(engine) as session:
            org = get_organization(session, org_id)
            if org is None or org.status != OrganizationStatus.ACTIVE:
                return None
            return build_system_context(
                session, organization_id=org.id, actor_type=ActorType.SYSTEM, source="execute_item",
            )
    except Exception:  # noqa: BLE001 - any failure must fail closed, never fall back
        return None


def _whatsapp_client(context=None) -> WhatsAppBusinessService:
    context = context or _current_tenant_context()
    if context is None:
        return WhatsAppBusinessService()
    from services.integration_clients import get_whatsapp_client

    return get_whatsapp_client(context)


def _gmail_client(context=None) -> GmailService:
    context = context or _current_tenant_context()
    if context is None:
        return GmailService()
    from services.integration_clients import get_gmail_client

    return get_gmail_client(context)


def _calendar_client(context=None) -> GoogleCalendarService:
    context = context or _current_tenant_context()
    if context is None:
        return GoogleCalendarService()
    from services.integration_clients import get_calendar_client

    return get_calendar_client(context)

ALLOWED_ACTIONS = {
    "calendar": {"create_event"},
    "gmail": {"create_draft", "send_email"},
    "whatsapp": {"send_text"},
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _load() -> list[dict[str, Any]]:
    return list(load_memory().get("execution_queue", []))


def _save(rows: list[dict[str, Any]]) -> None:
    trimmed = rows[-2000:]

    def mutate(memory: dict[str, Any]) -> None:
        memory["execution_queue"] = trimmed

    update_memory(mutate)


def _validate(
    provider: str,
    action: str,
    payload: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    clean_provider = _clean(provider, 40).casefold()
    clean_action = _clean(action, 60).casefold()
    if clean_provider not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported provider: {clean_provider or 'missing'}")
    if clean_action not in ALLOWED_ACTIONS[clean_provider]:
        raise ValueError(
            f"Unsupported {clean_provider} action: "
            f"{clean_action or 'missing'}"
        )
    if not isinstance(payload, dict):
        raise ValueError("Action payload must be an object.")

    clean_payload = copy.deepcopy(payload)
    required = {
        ("calendar", "create_event"): ("summary", "start", "end"),
        ("gmail", "create_draft"): ("to", "subject", "body"),
        ("gmail", "send_email"): ("to", "subject", "body"),
        ("whatsapp", "send_text"): ("to", "body"),
    }[(clean_provider, clean_action)]
    missing = [
        key for key in required
        if not _clean(clean_payload.get(key))
    ]
    if missing:
        raise ValueError(
            "Missing required action fields: " + ", ".join(missing)
        )
    return clean_provider, clean_action, clean_payload


def _fingerprint(
    provider: str,
    action: str,
    payload: dict[str, Any],
) -> str:
    canonical = json.dumps(
        [provider, action, payload],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def integration_statuses() -> list[dict[str, Any]]:
    return [
        _calendar_client().status(),
        _gmail_client().status(),
        _whatsapp_client().status(),
    ]


def prepare_execution(
    provider: str,
    action: str,
    payload: dict[str, Any],
    title: str = "",
    *,
    recommendation_id: str = "",
    impact: str = "",
    tenant_context=None,
) -> dict[str, Any]:
    """Validate and store an action, then create its unique approval.

    Phase 6.1: every prepared action is stamped with the resolved
    organization_id (explicit `tenant_context`, or the same transitional
    default every existing caller has always effectively used) on both
    the approval and the queue item, so execute_item() can later derive
    its own execution context strictly from the item — never from an
    ambient default. If no organization can be resolved at all (e.g.
    the database is unreachable), the action is deliberately not
    prepared: creating an item with no provable tenant identity would
    only defer today's failure into a confusing one at execution time."""
    provider, action, payload = _validate(provider, action, payload)
    fingerprint = _fingerprint(provider, action, payload)
    rows = _load()
    existing = next(
        (
            row for row in rows
            if row.get("fingerprint") == fingerprint
            and row.get("status") in {
                "Awaiting approval",
                "Approved",
            }
        ),
        None,
    )
    if existing:
        return copy.deepcopy(existing)

    context = tenant_context or _current_tenant_context()
    if context is None:
        raise RuntimeError(
            "Could not resolve an organization context; action was not prepared."
        )

    item_id = f"EXEC-{uuid4().hex[:10].upper()}"
    clean_title = _clean(
        title or action.replace("_", " ").title(),
        300,
    )
    risk_level = (
        "Medium"
        if provider == "gmail" and action == "create_draft"
        else "High"
    )
    approval = add_memory_entry("approvals", {
        "title": clean_title,
        "department": "Integrations",
        "risk_level": risk_level,
        "status": "Pending",
        "execution_id": item_id,
        "recommendation_id": _clean(recommendation_id, 40),
        "organization_id": context.organization_id,
    })
    item = {
        "id": item_id,
        "provider": provider,
        "action": action,
        "payload": payload,
        "title": clean_title,
        "impact": _clean(impact, 800),
        "recommendation_id": _clean(recommendation_id, 40),
        "approval_id": approval.get("id", ""),
        "approval_status": "Pending",
        "status": "Awaiting approval",
        "fingerprint": fingerprint,
        "organization_id": context.organization_id,
        "created_at": _now(),
        "approved_at": "",
        "executed_at": "",
        "result": None,
    }
    rows.append(item)
    _save(rows)
    audit_event("local-owner", "prepare_execution", provider, item_id)
    _shadow_sync_approval_and_item(approval, item)
    return copy.deepcopy(item)


def _approval_status(approval_id: str) -> str:
    for row in load_memory().get("approvals", []):
        if row.get("id") == approval_id:
            return str(row.get("data", {}).get("status", "Pending"))
    return "Missing"


def _get_approval_row(approval_id: str) -> dict[str, Any] | None:
    for row in load_memory().get("approvals", []):
        if row.get("id") == approval_id:
            return row
    return None


def _shadow_sync_approval_and_item(approval: dict[str, Any] | None, item: dict[str, Any]) -> None:
    """Phase 5: best-effort tenant-scoped relational shadow copy, called
    only after the legacy write above has already succeeded. Never
    raises — see services/tenant_operational_sync.py."""
    try:
        from services.tenant_operational_sync import sync_approval, sync_execution_queue_item

        if approval is not None:
            sync_approval(approval)
        sync_execution_queue_item(item)
    except Exception:  # noqa: BLE001 - must never break a live approval/execution operation
        pass


def decide_item(item_id: str, decision: str) -> dict[str, Any]:
    """Approve or reject a prepared action."""
    canonical = _clean(decision, 20).title()
    if canonical not in {"Approved", "Rejected"}:
        raise ValueError("Decision must be Approved or Rejected.")
    rows = _load()
    item = next((row for row in rows if row.get("id") == item_id), None)
    if item is None:
        return {
            "success": False,
            "status": "not_found",
            "detail": "Prepared action was not found.",
        }
    if item.get("status") in {"Sent", "Simulated", "Failed"}:
        return {
            "success": False,
            "status": "already_executed",
            "detail": "An executed action cannot be re-decided.",
        }
    update_approval_status(item.get("approval_id", ""), canonical)
    item["approval_status"] = canonical
    item["status"] = canonical if canonical == "Approved" else "Rejected"
    item["approved_at"] = _now() if canonical == "Approved" else ""
    item["rejected_at"] = _now() if canonical == "Rejected" else ""
    _save(rows)
    audit_event(
        "local-owner",
        canonical.casefold(),
        item.get("provider", ""),
        item_id,
    )
    _shadow_sync_approval_and_item(_get_approval_row(item.get("approval_id", "")), item)
    return copy.deepcopy(item)


def execute_item(item_id: str) -> dict[str, Any]:
    """Execute exactly once, and only after approval."""
    rows = _load()
    item = next((row for row in rows if row.get("id") == item_id), None)
    if not item:
        return {
            "success": False,
            "status": "not_found",
            "detail": "Prepared action was not found.",
        }
    if item.get("status") in {"Sent", "Simulated", "Failed"}:
        return copy.deepcopy(item.get("result") or {
            "success": item.get("status") in {"Sent", "Simulated"},
            "status": item.get("status"),
        })

    approval_status = _approval_status(item.get("approval_id", ""))
    if approval_status.casefold() != "approved":
        return {
            "success": False,
            "status": "blocked",
            "detail": f"Approval status is {approval_status}.",
        }

    # Phase 6.1: derive the execution TenantContext strictly from the
    # item's own stamped organization_id — see
    # _resolve_item_execution_context()'s docstring for the trust
    # boundary this enforces. Never falls back to the transitional
    # default or any other organization.
    context = _resolve_item_execution_context(item)
    if context is None:
        return {
            "success": False,
            "status": "blocked",
            "detail": "Could not resolve a valid, active organization for this queued action.",
        }

    # Section 7 defense-in-depth: an approval and its queue item are
    # always stamped with the same organization_id at creation time
    # (prepare_execution()) — this is a belt-and-braces check, not the
    # primary mechanism, since nothing in this codebase can create a
    # mismatched pair through normal use.
    approval_row = _get_approval_row(item.get("approval_id", ""))
    approval_org_id = (approval_row or {}).get("data", {}).get("organization_id")
    if approval_org_id is not None and approval_org_id != context.organization_id:
        return {
            "success": False,
            "status": "blocked",
            "detail": "Approval organization does not match queue item organization.",
        }

    provider, action, payload = _validate(
        item["provider"],
        item["action"],
        item["payload"],
    )
    if provider == "calendar":
        result = _calendar_client(context).create_event(payload)
    elif provider == "gmail":
        service = _gmail_client(context)
        result = (
            service.create_draft(payload)
            if action == "create_draft"
            else service.send_email(payload)
        )
    else:
        result = _whatsapp_client(context).send_text(payload)

    result_dict = result.to_dict()
    item["result"] = result_dict
    item["approval_status"] = "Approved"
    item["status"] = (
        "Sent"
        if result.success and result.status == "sent"
        else "Simulated"
        if result.success
        else "Failed"
    )
    item["executed_at"] = _now()
    _save(rows)
    add_memory_entry("reports", {
        "type": "Integration Execution",
        "title": item["title"],
        "provider": provider,
        "action": action,
        "status": item["status"],
        "execution_id": item_id,
        "recommendation_id": item.get("recommendation_id", ""),
        "external_id": result.external_id,
        "detail": result.detail,
    })
    record_action_execution(
        item.get("recommendation_id", ""),
        item_id,
        provider,
        action,
        item["status"],
        result_dict,
    )
    audit_event(
        "local-owner",
        "execute",
        provider,
        f"{item_id}:{item['status']}",
    )
    _shadow_sync_approval_and_item(_get_approval_row(item.get("approval_id", "")), item)
    return copy.deepcopy(result_dict)


def execution_rows() -> list[dict[str, Any]]:
    rows = _load()
    changed = False
    for item in rows:
        status = _approval_status(item.get("approval_id", ""))
        if item.get("approval_status") != status:
            item["approval_status"] = status
            if (
                status == "Approved"
                and item.get("status") == "Awaiting approval"
            ):
                item["status"] = "Approved"
            elif (
                status == "Rejected"
                and item.get("status") in {
                    "Awaiting approval",
                    "Approved",
                }
            ):
                item["status"] = "Rejected"
            changed = True
    if changed:
        _save(rows)
    return list(reversed(copy.deepcopy(rows)))
