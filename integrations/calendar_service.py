from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from integrations.base import IntegrationResult


class GoogleCalendarService:
    """Google Calendar adapter with a safe dry-run mode.

    Live mode uses a Google service-account JSON file and an explicitly shared
    calendar. The calendar must be shared with the service-account email.
    """

    def __init__(self, dry_run: bool | None = None, *, credentials: dict[str, Any] | None = None) -> None:
        """`credentials`, when given (Phase 6 — see
        services/integration_clients.py), supplies a per-organization
        {"service_account_json" or "service_account_file", "calendar_id"}
        instead of this reading deployment-wide environment variables.
        Omitting it reproduces exactly the pre-Phase-6 behavior."""
        source = credentials or {}
        self.credentials_json = str(source.get("service_account_json") or "").strip()
        self.credentials_path = str(source.get("service_account_file") or "").strip() or (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() if not self.credentials_json else ""
        )
        self.calendar_id = str(source.get("calendar_id") or os.getenv("GOOGLE_CALENDAR_ID", "primary")).strip() or "primary"
        has_credential_source = bool(self.credentials_json) or bool(self.credentials_path and Path(self.credentials_path).exists())
        configured = has_credential_source
        self.dry_run = (not configured) if dry_run is None else dry_run

    def _service_account_credentials(self, scopes: list[str]):
        from google.oauth2 import service_account

        if self.credentials_json:
            info = json.loads(self.credentials_json)
            return service_account.Credentials.from_service_account_info(info, scopes=scopes)
        return service_account.Credentials.from_service_account_file(self.credentials_path, scopes=scopes)

    def status(self) -> dict[str, Any]:
        has_credential_source = bool(self.credentials_json) or bool(self.credentials_path and Path(self.credentials_path).exists())
        return {
            "provider": "Google Calendar",
            "configured": has_credential_source,
            "mode": "dry-run" if self.dry_run else "live",
            "calendar_id": self.calendar_id,
        }

    def create_event(self, payload: dict[str, Any]) -> IntegrationResult:
        required = ["summary", "start", "end"]
        missing = [key for key in required if not payload.get(key)]
        if missing:
            return IntegrationResult("calendar", "create_event", False, "validation_failed", detail=f"Missing: {', '.join(missing)}", payload=payload)

        event = {
            "summary": payload["summary"],
            "description": payload.get("description", ""),
            "location": payload.get("location", ""),
            "start": {"dateTime": payload["start"], "timeZone": payload.get("timezone", "Asia/Kolkata")},
            "end": {"dateTime": payload["end"], "timeZone": payload.get("timezone", "Asia/Kolkata")},
        }
        if payload.get("attendees"):
            event["attendees"] = [{"email": email} for email in payload["attendees"]]

        if self.dry_run:
            return IntegrationResult("calendar", "create_event", True, "simulated", detail="Calendar event validated; no external event created.", payload=event)

        try:
            from googleapiclient.discovery import build

            credentials = self._service_account_credentials(["https://www.googleapis.com/auth/calendar"])
            service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
            created = service.events().insert(calendarId=self.calendar_id, body=event, sendUpdates="all").execute()
            return IntegrationResult("calendar", "create_event", True, "sent", external_id=created.get("id", ""), detail=created.get("htmlLink", ""), payload=event)
        except Exception as exc:
            return IntegrationResult("calendar", "create_event", False, "failed", detail=str(exc), payload=event)
