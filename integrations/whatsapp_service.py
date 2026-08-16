from __future__ import annotations

import os
from typing import Any

import requests

from integrations.base import IntegrationResult


class WhatsAppBusinessService:
    """Meta WhatsApp Cloud API adapter with dry-run fallback."""

    def __init__(self, dry_run: bool | None = None, *, credentials: dict[str, Any] | None = None) -> None:
        """`credentials`, when given (Phase 6 — see
        services/integration_clients.py), supplies
        {"access_token", "phone_number_id", "api_version"} resolved for a
        specific organization instead of this reading the deployment-wide
        environment variables directly. Omitting it reproduces exactly
        the pre-Phase-6 behavior — every existing caller that constructs
        this class with no arguments is unaffected."""
        source = credentials or {}
        self.token = str(source.get("access_token") or os.getenv("WHATSAPP_ACCESS_TOKEN", "")).strip()
        self.phone_number_id = str(source.get("phone_number_id") or os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")).strip()
        self.api_version = str(source.get("api_version") or os.getenv("WHATSAPP_API_VERSION", "v23.0")).strip() or "v23.0"
        configured = bool(self.token and self.phone_number_id)
        self.dry_run = (not configured) if dry_run is None else dry_run

    def status(self) -> dict[str, Any]:
        return {"provider": "WhatsApp Business", "configured": bool(self.token and self.phone_number_id), "mode": "dry-run" if self.dry_run else "live", "phone_number_id": self.phone_number_id}

    def send_text(self, payload: dict[str, Any]) -> IntegrationResult:
        to = str(payload.get("to", "")).replace("+", "").replace(" ", "")
        body = str(payload.get("body", "")).strip()
        if not to or not body:
            return IntegrationResult("whatsapp", "send_text", False, "validation_failed", detail="to and body are required", payload=payload)
        request_body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text", "text": {"preview_url": False, "body": body}}
        if self.dry_run:
            return IntegrationResult("whatsapp", "send_text", True, "simulated", detail="WhatsApp message validated; no message sent.", payload=request_body)
        try:
            url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
            response = requests.post(url, headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}, json=request_body, timeout=30)
            data = response.json() if response.content else {}
            if response.ok:
                message_id = (data.get("messages") or [{}])[0].get("id", "")
                return IntegrationResult("whatsapp", "send_text", True, "sent", external_id=message_id, payload=request_body)
            return IntegrationResult("whatsapp", "send_text", False, "failed", detail=f"HTTP {response.status_code}: {data}", payload=request_body)
        except Exception as exc:
            return IntegrationResult("whatsapp", "send_text", False, "failed", detail=str(exc), payload=request_body)
