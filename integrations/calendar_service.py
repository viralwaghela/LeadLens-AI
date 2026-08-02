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

    def __init__(self, dry_run: bool | None = None) -> None:
        self.credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"
        configured = bool(self.credentials_path and Path(self.credentials_path).exists())
        self.dry_run = (not configured) if dry_run is None else dry_run

    def status(self) -> dict[str, Any]:
        return {
            "provider": "Google Calendar",
            "configured": bool(self.credentials_path and Path(self.credentials_path).exists()),
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
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
            service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
            created = service.events().insert(calendarId=self.calendar_id, body=event, sendUpdates="all").execute()
            return IntegrationResult("calendar", "create_event", True, "sent", external_id=created.get("id", ""), detail=created.get("htmlLink", ""), payload=event)
        except Exception as exc:
            return IntegrationResult("calendar", "create_event", False, "failed", detail=str(exc), payload=event)
