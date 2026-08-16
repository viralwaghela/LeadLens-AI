from __future__ import annotations

import base64
import json
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from integrations.base import IntegrationResult


class GmailService:
    """Gmail adapter using a service account with domain-wide delegation.

    In local pilots without credentials the adapter runs in dry-run mode.
    """

    def __init__(self, dry_run: bool | None = None, *, credentials: dict[str, Any] | None = None) -> None:
        """`credentials`, when given (Phase 6 — see
        services/integration_clients.py), supplies a per-organization
        {"service_account_json" or "service_account_file", "delegated_user"}
        instead of this reading deployment-wide environment variables.
        Omitting it reproduces exactly the pre-Phase-6 behavior."""
        source = credentials or {}
        self.credentials_json = str(source.get("service_account_json") or "").strip()
        self.credentials_path = str(source.get("service_account_file") or "").strip() or (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() if not self.credentials_json else ""
        )
        self.delegated_user = str(source.get("delegated_user") or os.getenv("GMAIL_DELEGATED_USER", "")).strip()
        has_credential_source = bool(self.credentials_json) or bool(self.credentials_path and Path(self.credentials_path).exists())
        configured = bool(has_credential_source and self.delegated_user)
        self.dry_run = (not configured) if dry_run is None else dry_run

    def _service_account_credentials(self, scopes: list[str]):
        from google.oauth2 import service_account

        if self.credentials_json:
            info = json.loads(self.credentials_json)
            return service_account.Credentials.from_service_account_info(
                info, scopes=scopes, subject=self.delegated_user,
            )
        return service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=scopes, subject=self.delegated_user,
        )

    def status(self) -> dict[str, Any]:
        has_credential_source = bool(self.credentials_json) or bool(self.credentials_path and Path(self.credentials_path).exists())
        return {"provider": "Gmail", "configured": bool(has_credential_source and self.delegated_user), "mode": "dry-run" if self.dry_run else "live", "delegated_user": self.delegated_user}

    @staticmethod
    def _raw_message(payload: dict[str, Any]) -> str:
        msg = EmailMessage()
        msg["To"] = payload.get("to", "")
        msg["Subject"] = payload.get("subject", "")
        if payload.get("cc"):
            msg["Cc"] = payload["cc"]
        msg.set_content(payload.get("body", ""))
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def create_draft(self, payload: dict[str, Any]) -> IntegrationResult:
        if not payload.get("to") or not payload.get("subject") or not payload.get("body"):
            return IntegrationResult("gmail", "create_draft", False, "validation_failed", detail="to, subject and body are required", payload=payload)
        if self.dry_run:
            return IntegrationResult("gmail", "create_draft", True, "simulated", detail="Email draft validated; no Gmail draft created.", payload=payload)
        return self._execute("draft", payload)

    def send_email(self, payload: dict[str, Any]) -> IntegrationResult:
        if not payload.get("to") or not payload.get("subject") or not payload.get("body"):
            return IntegrationResult("gmail", "send_email", False, "validation_failed", detail="to, subject and body are required", payload=payload)
        if self.dry_run:
            return IntegrationResult("gmail", "send_email", True, "simulated", detail="Email validated; no email sent.", payload=payload)
        return self._execute("send", payload)

    def _execute(self, mode: str, payload: dict[str, Any]) -> IntegrationResult:
        try:
            from googleapiclient.discovery import build

            credentials = self._service_account_credentials(
                ["https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.send"],
            )
            service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
            raw = {"raw": self._raw_message(payload)}
            if mode == "draft":
                response = service.users().drafts().create(userId="me", body={"message": raw}).execute()
                ext_id = response.get("id", "")
                action = "create_draft"
            else:
                response = service.users().messages().send(userId="me", body=raw).execute()
                ext_id = response.get("id", "")
                action = "send_email"
            return IntegrationResult("gmail", action, True, "sent", external_id=ext_id, payload=payload)
        except Exception as exc:
            return IntegrationResult("gmail", "create_draft" if mode == "draft" else "send_email", False, "failed", detail=str(exc), payload=payload)
