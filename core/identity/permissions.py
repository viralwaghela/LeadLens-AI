"""Backend permission taxonomy and role -> permission matrix for Phase 1.

Provider-neutral, backend-only permission strings — not tied to any UI
element yet (nothing renders differently based on these today). See
docs/V2_PHASE1_IDENTITY.md for the reasoning behind each role's grants.

This intentionally does NOT reuse services/security_service.py's
ROLE_PERMISSIONS dict: that dict's permission names (view_finance,
manage_users, ...) are coarse, its role set (Owner/Therapist/
Receptionist/Viewer) doesn't cover a finance/marketing-oriented SaaS
role model, and — most importantly — nothing in the live app actually
enforces it today (confirmed: mask_sensitive/audit_event/audit_rows are
the only functions services/security_service.py exports; no call site
gates on ROLE_PERMISSIONS). Phase 1 builds a real, tested, minimum
coherent taxonomy instead of inheriting an unenforced one.
"""
from __future__ import annotations

from core.db.models.identity import MembershipRole

PERMISSIONS: frozenset[str] = frozenset(
    {
        "organization.view",
        "organization.manage",
        "members.view",
        "members.manage",
        "patients.view",
        "patients.manage",
        "appointments.view",
        "appointments.manage",
        "treatments.view",
        "treatments.manage",
        "payments.view",
        "payments.manage",
        "finance.view",
        "leads.view",
        "leads.manage",
        "automations.view",
        "automations.approve",
        "automations.manage",
        "integrations.view",
        "integrations.manage",
        "jarvis.use",
        "jarvis.finance",
        "jarvis.operations",
        "jarvis.marketing",
        "audit.view",
    }
)

ROLE_PERMISSIONS: dict[MembershipRole, frozenset[str]] = {
    # Full access. The only role that can manage the organization itself
    # or hand out/revoke other memberships.
    MembershipRole.OWNER: frozenset(PERMISSIONS),
    # Day-to-day operational manager. Can run the clinic (patients,
    # appointments, treatments, leads, automations, integrations,
    # members) but cannot rename/deactivate the organization, cannot see
    # or manage payments/finance, and has no finance-sensitive Jarvis
    # access — those stay Owner/Finance-only.
    MembershipRole.ADMIN: frozenset(
        {
            "organization.view",
            "members.view",
            "members.manage",
            "patients.view",
            "patients.manage",
            "appointments.view",
            "appointments.manage",
            "treatments.view",
            "treatments.manage",
            "payments.view",
            "leads.view",
            "leads.manage",
            "automations.view",
            "automations.approve",
            "automations.manage",
            "integrations.view",
            "integrations.manage",
            "jarvis.use",
            "jarvis.operations",
            "jarvis.marketing",
            "audit.view",
        }
    ),
    # Front-desk role: patients, appointments, leads, viewing payments
    # (e.g. confirming a package balance at checkout) — but no finance
    # visibility, no member/integration management, no automation
    # approval authority.
    MembershipRole.RECEPTIONIST: frozenset(
        {
            "organization.view",
            "patients.view",
            "patients.manage",
            "appointments.view",
            "appointments.manage",
            "treatments.view",
            "payments.view",
            "leads.view",
            "leads.manage",
            "automations.view",
            "jarvis.use",
            "jarvis.operations",
        }
    ),
    # Clinical role: patients, appointments, treatment/progress notes.
    # No leads, no payments, no finance, no member/integration
    # management.
    MembershipRole.PRACTITIONER: frozenset(
        {
            "organization.view",
            "patients.view",
            "patients.manage",
            "appointments.view",
            "appointments.manage",
            "treatments.view",
            "treatments.manage",
            "jarvis.use",
            "jarvis.operations",
        }
    ),
    # Finance-sensitive role: payments, finance dashboards, the
    # finance-flavored Jarvis tool tier, and audit visibility. No
    # patient/appointment/clinical access — a bookkeeper doesn't need it.
    MembershipRole.FINANCE: frozenset(
        {
            "organization.view",
            "payments.view",
            "payments.manage",
            "finance.view",
            "audit.view",
            "jarvis.use",
            "jarvis.finance",
        }
    ),
    # Leads/growth role: leads and the marketing-flavored Jarvis tool
    # tier. No patient/payment/finance access.
    MembershipRole.MARKETING: frozenset(
        {
            "organization.view",
            "leads.view",
            "leads.manage",
            "automations.view",
            "jarvis.use",
            "jarvis.marketing",
        }
    ),
    # Read-only across non-financial clinic data. No payments/finance
    # (financial data is not a "view everything" default), no manage
    # permissions of any kind.
    MembershipRole.VIEWER: frozenset(
        {
            "organization.view",
            "patients.view",
            "appointments.view",
            "treatments.view",
            "leads.view",
            "automations.view",
            "jarvis.use",
        }
    ),
}


def permissions_for_role(role: MembershipRole) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def role_has_permission(role: MembershipRole, permission: str) -> bool:
    return permission in permissions_for_role(role)
