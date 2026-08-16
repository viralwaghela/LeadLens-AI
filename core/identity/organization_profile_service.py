"""Phase 8 — organization-scoped clinic/company profile CRUD.

Backs services.platform_data.save_company_profile()/business_snapshot()
when LEADLENS_V2_ORG_SCOPED_SETTINGS_ENABLED is on and a live
organization resolves (core.identity.live_organization) — the fix for
the Phase 8 audit's core finding: core/memory.py's `company` dict is one
global object per deployment, which cannot hold two organizations'
settings at once. This module makes core/db/models/organization.py's
Phase 0 `OrganizationSettings` table (previously dormant — "nothing in
the live app creates, reads, or references this table yet") the live,
per-organization store instead.

Known/promoted columns win over anything with the same key parked in
`extra` (the JSON catch-all for fields Phase 0 didn't promote to real
columns) — `extra` only ever holds the remainder.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from core.db.models.organization import OrganizationSettings

_KNOWN_COLUMNS = {
    "business_name",
    "industry",
    "location",
    "website",
    "google_review_link",
    "monthly_revenue",
    "monthly_expenses",
    "target_monthly_revenue",
}


def _get_row(session: Session, organization_id: int) -> OrganizationSettings | None:
    return (
        session.query(OrganizationSettings)
        .filter(OrganizationSettings.organization_id == organization_id)
        .one_or_none()
    )


def settings_exist(session: Session, organization_id: int) -> bool:
    return (
        session.query(OrganizationSettings.id)
        .filter(OrganizationSettings.organization_id == organization_id)
        .first()
        is not None
    )


def get_settings(session: Session, organization_id: int) -> dict[str, Any]:
    row = _get_row(session, organization_id)
    if row is None:
        return {}
    out: dict[str, Any] = {}
    if row.extra:
        try:
            out.update(json.loads(row.extra))
        except (ValueError, TypeError):
            pass
    out.update(
        {
            "business_name": row.business_name or "",
            "industry": row.industry or "",
            "location": row.location or "",
            "website": row.website or "",
            "google_review_link": row.google_review_link or "",
            "monthly_revenue": row.monthly_revenue or 0,
            "monthly_expenses": row.monthly_expenses or 0,
            "target_monthly_revenue": row.target_monthly_revenue or 0,
        }
    )
    return out


def save_settings(session: Session, organization_id: int, profile: dict[str, Any]) -> OrganizationSettings:
    row = _get_row(session, organization_id)
    if row is None:
        row = OrganizationSettings(organization_id=organization_id)
        session.add(row)
    row.business_name = str(profile.get("business_name") or "").strip() or None
    row.industry = str(profile.get("industry") or "").strip() or None
    row.location = str(profile.get("location") or "").strip() or None
    row.website = str(profile.get("website") or "").strip() or None
    row.google_review_link = str(profile.get("google_review_link") or "").strip() or None
    row.monthly_revenue = float(profile.get("monthly_revenue") or 0) or None
    row.monthly_expenses = float(profile.get("monthly_expenses") or 0) or None
    row.target_monthly_revenue = float(profile.get("target_monthly_revenue") or 0) or None
    extra = {k: v for k, v in profile.items() if k not in _KNOWN_COLUMNS}
    row.extra = json.dumps(extra) if extra else None
    session.flush()
    return row


def automations_enabled(session: Session, organization_id: int) -> bool:
    row = _get_row(session, organization_id)
    return bool(row.automations_enabled) if row is not None else False


def set_automations_enabled(session: Session, organization_id: int, enabled: bool) -> None:
    row = _get_row(session, organization_id)
    if row is None:
        row = OrganizationSettings(organization_id=organization_id)
        session.add(row)
    row.automations_enabled = bool(enabled)
    session.flush()
