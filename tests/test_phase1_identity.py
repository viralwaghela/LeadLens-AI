"""V2 Phase 1 identity/authorization tests.

Schema-and-service-boundary tests only, exactly like
tests/test_phase0_schema.py: they prove core/identity/'s services and
core/db/models/identity*.py's schema work correctly in isolation. They
do NOT prove live application behavior changed, because nothing in the
live app (core/auth.py, app.py, dashboard.py, services/, ui/,
scheduler/) imports anything from core/identity/ or reads these tables
yet. See docs/V2_PHASE1_IDENTITY.md.

Every test uses its own private, temporary SQLite database (in-memory
for direct service calls, a real temp file for the bootstrap-script
subprocess tests) — never core/memory.py's database, never a real
DATABASE_URL. import _bootstrap first, same as every other file in
tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole, MembershipStatus, User, UserStatus
from core.db.models.organization import Organization, OrganizationStatus
from core.db.session import make_engine

from core.identity import (
    authentication_service,
    authorization_service,
    membership_service,
    organization_service,
    user_service,
)
from core.identity.password_service import hash_password, verify_password
from core.identity.permissions import ROLE_PERMISSIONS, permissions_for_role
from core.identity.user_service import DuplicateUserError
from core.identity.organization_service import DuplicateOrganizationError, OrganizationNotFoundError
from core.identity.membership_service import DuplicateMembershipError

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def engine():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def test_valid_password_verifies() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_incorrect_password_fails() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password("wrong password", h) is False


def test_hash_differs_from_plaintext() -> None:
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert "correct horse battery staple" not in h


def test_two_hashes_of_same_password_are_salted_differently() -> None:
    h1 = hash_password("same password")
    h2 = hash_password("same password")
    assert h1 != h2  # different random salts
    assert verify_password("same password", h1) is True
    assert verify_password("same password", h2) is True


def test_empty_password_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_disabled_users_cannot_authenticate_through_identity_service(session) -> None:
    org = organization_service.create_organization(session, name="Clinic", slug="clinic")
    user = user_service.create_user(session, email="staff@example.com", password="s3cret-pass")
    membership_service.create_membership(
        session, user_id=user.id, organization_id=org.id, role=MembershipRole.VIEWER
    )
    user_service.disable_user(session, user.id)

    result = authentication_service.authenticate(session, email="staff@example.com", password="s3cret-pass")
    assert result.success is False
    assert result.reason == "user_disabled"


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def test_create_active_user(session) -> None:
    user = user_service.create_user(session, email="Owner@Example.com", password="hunter2pass")
    assert user.id is not None
    assert user.status == UserStatus.ACTIVE
    assert user.email == "owner@example.com"  # normalized
    assert user.password_hash != "hunter2pass"


def test_duplicate_email_rejected(session) -> None:
    user_service.create_user(session, email="dup@example.com", password="pw-one-two-three")
    with pytest.raises(DuplicateUserError):
        user_service.create_user(session, email="DUP@example.com", password="different-pw")


def test_disable_user(session) -> None:
    user = user_service.create_user(session, email="a@example.com", password="pw-one-two-three")
    user_service.disable_user(session, user.id)
    assert user.status == UserStatus.DISABLED


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

def test_create_organization(session) -> None:
    org = organization_service.create_organization(session, name="Beyond Pain", slug="beyond-pain")
    assert org.id is not None
    assert org.status == OrganizationStatus.ACTIVE


def test_duplicate_organization_slug_rejected(session) -> None:
    organization_service.create_organization(session, name="Clinic A", slug="clinic-a")
    with pytest.raises(DuplicateOrganizationError):
        organization_service.create_organization(session, name="Clinic A Copy", slug="clinic-a")


def test_deactivate_organization(session) -> None:
    org = organization_service.create_organization(session, name="Clinic A", slug="clinic-a")
    organization_service.deactivate_organization(session, org.id)
    assert org.status == OrganizationStatus.INACTIVE


def test_invalid_organization_operations_raise(session) -> None:
    with pytest.raises(OrganizationNotFoundError):
        organization_service.deactivate_organization(session, 999999)
    assert organization_service.get_organization(session, 999999) is None


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------

def test_create_membership(session) -> None:
    org = organization_service.create_organization(session, name="Clinic A", slug="clinic-a")
    user = user_service.create_user(session, email="a@example.com", password="pw-one-two-three")
    membership = membership_service.create_membership(
        session, user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER
    )
    assert membership.id is not None
    assert membership.status == MembershipStatus.ACTIVE


def test_prevent_duplicate_membership(session) -> None:
    org = organization_service.create_organization(session, name="Clinic A", slug="clinic-a")
    user = user_service.create_user(session, email="a@example.com", password="pw-one-two-three")
    membership_service.create_membership(
        session, user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER
    )
    with pytest.raises(DuplicateMembershipError):
        membership_service.create_membership(
            session, user_id=user.id, organization_id=org.id, role=MembershipRole.VIEWER
        )


def test_disabled_membership_denies_access(session) -> None:
    org = organization_service.create_organization(session, name="Clinic A", slug="clinic-a")
    user = user_service.create_user(session, email="a@example.com", password="pw-one-two-three")
    membership = membership_service.create_membership(
        session, user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER
    )
    membership_service.disable_membership(session, membership.id)

    decision = authorization_service.authorize(
        session, user_id=user.id, organization_id=org.id, permission="organization.view"
    )
    assert decision.allowed is False
    assert decision.reason == "membership_disabled"


def test_user_can_belong_to_multiple_organizations(session) -> None:
    org_a = organization_service.create_organization(session, name="Org A", slug="org-a")
    org_b = organization_service.create_organization(session, name="Org B", slug="org-b")
    user = user_service.create_user(session, email="consultant@example.com", password="pw-one-two-three")

    membership_service.create_membership(session, user_id=user.id, organization_id=org_a.id, role=MembershipRole.OWNER)
    membership_service.create_membership(session, user_id=user.id, organization_id=org_b.id, role=MembershipRole.VIEWER)

    memberships = membership_service.list_memberships_for_user(session, user.id)
    assert len(memberships) == 2
    assert {m.organization_id for m in memberships} == {org_a.id, org_b.id}


# ---------------------------------------------------------------------------
# RBAC — role -> permission matrix
# ---------------------------------------------------------------------------

def test_role_permission_matrix_covers_every_role() -> None:
    assert set(ROLE_PERMISSIONS.keys()) == set(MembershipRole)


def test_owner_has_every_permission() -> None:
    from core.identity.permissions import PERMISSIONS

    assert permissions_for_role(MembershipRole.OWNER) == PERMISSIONS


def test_receptionist_restrictions() -> None:
    perms = permissions_for_role(MembershipRole.RECEPTIONIST)
    assert "patients.manage" in perms
    assert "appointments.manage" in perms
    assert "finance.view" not in perms
    assert "payments.manage" not in perms
    assert "jarvis.finance" not in perms
    assert "organization.manage" not in perms
    assert "members.manage" not in perms


def test_practitioner_restrictions() -> None:
    perms = permissions_for_role(MembershipRole.PRACTITIONER)
    assert "treatments.manage" in perms
    assert "payments.view" not in perms
    assert "leads.view" not in perms
    assert "finance.view" not in perms
    assert "members.manage" not in perms


def test_finance_restrictions() -> None:
    perms = permissions_for_role(MembershipRole.FINANCE)
    assert "payments.manage" in perms
    assert "finance.view" in perms
    assert "patients.manage" not in perms
    assert "appointments.manage" not in perms
    assert "jarvis.marketing" not in perms


def test_viewer_restrictions() -> None:
    perms = permissions_for_role(MembershipRole.VIEWER)
    assert "patients.view" in perms
    assert "patients.manage" not in perms
    assert "payments.view" not in perms
    assert "finance.view" not in perms


def test_admin_behavior() -> None:
    perms = permissions_for_role(MembershipRole.ADMIN)
    assert "members.manage" in perms
    assert "automations.manage" in perms
    assert "organization.manage" not in perms  # owner-only
    assert "payments.manage" not in perms  # finance/owner-only
    assert "finance.view" not in perms


# ---------------------------------------------------------------------------
# Adversarial authorization tests (spec section 9)
# ---------------------------------------------------------------------------

@pytest.fixture()
def two_org_fixture(session):
    """Organization A / B, Owner A / Receptionist A / Practitioner A, Owner B."""
    org_a = organization_service.create_organization(session, name="Org A", slug="org-a")
    org_b = organization_service.create_organization(session, name="Org B", slug="org-b")

    owner_a = user_service.create_user(session, email="owner-a@example.com", password="pw-owner-a-123")
    receptionist_a = user_service.create_user(session, email="reception-a@example.com", password="pw-recep-a-123")
    practitioner_a = user_service.create_user(session, email="practitioner-a@example.com", password="pw-prac-a-123")
    owner_b = user_service.create_user(session, email="owner-b@example.com", password="pw-owner-b-123")

    m_owner_a = membership_service.create_membership(session, user_id=owner_a.id, organization_id=org_a.id, role=MembershipRole.OWNER)
    membership_service.create_membership(session, user_id=receptionist_a.id, organization_id=org_a.id, role=MembershipRole.RECEPTIONIST)
    membership_service.create_membership(session, user_id=practitioner_a.id, organization_id=org_a.id, role=MembershipRole.PRACTITIONER)
    membership_service.create_membership(session, user_id=owner_b.id, organization_id=org_b.id, role=MembershipRole.OWNER)

    return {
        "org_a": org_a,
        "org_b": org_b,
        "owner_a": owner_a,
        "receptionist_a": receptionist_a,
        "practitioner_a": practitioner_a,
        "owner_b": owner_b,
        "owner_a_membership": m_owner_a,
    }


def test_membership_isolation_owner_a_denied_org_b(session, two_org_fixture) -> None:
    f = two_org_fixture
    decision = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=f["org_b"].id, permission="organization.view"
    )
    assert decision.allowed is False
    assert decision.reason == "membership_not_found"


def test_disabled_user_denied(session, two_org_fixture) -> None:
    f = two_org_fixture
    user_service.disable_user(session, f["owner_a"].id)
    decision = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=f["org_a"].id, permission="organization.view"
    )
    assert decision.allowed is False
    assert decision.reason == "user_disabled"


def test_disabled_membership_denied(session, two_org_fixture) -> None:
    f = two_org_fixture
    membership_service.disable_membership(session, f["owner_a_membership"].id)
    decision = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=f["org_a"].id, permission="organization.view"
    )
    assert decision.allowed is False
    assert decision.reason == "membership_disabled"


def test_receptionist_finance_restriction_denied(session, two_org_fixture) -> None:
    f = two_org_fixture
    decision = authorization_service.authorize(
        session, user_id=f["receptionist_a"].id, organization_id=f["org_a"].id, permission="finance.view"
    )
    assert decision.allowed is False
    assert decision.reason == "permission_denied"


def test_owner_permitted_operations_allowed(session, two_org_fixture) -> None:
    f = two_org_fixture
    for permission in ("organization.manage", "finance.view", "members.manage", "payments.manage"):
        decision = authorization_service.authorize(
            session, user_id=f["owner_a"].id, organization_id=f["org_a"].id, permission=permission
        )
        assert decision.allowed is True, f"expected owner to be allowed {permission}, got {decision.reason}"


def test_cross_organization_membership_escalation_denied(session, two_org_fixture) -> None:
    """A user belonging only to Org A attempting to manage memberships
    inside Org B must be denied — manually supplying Org B's real ID
    does not help, because no membership exists there."""
    f = two_org_fixture
    decision = authorization_service.authorize(
        session, user_id=f["receptionist_a"].id, organization_id=f["org_b"].id, permission="members.manage"
    )
    assert decision.allowed is False
    assert decision.reason == "membership_not_found"

    # Even Owner A — who legitimately has members.manage in Org A — is
    # denied the same permission in Org B.
    decision_owner = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=f["org_b"].id, permission="members.manage"
    )
    assert decision_owner.allowed is False
    assert decision_owner.reason == "membership_not_found"


def test_inactive_organization_denied(session, two_org_fixture) -> None:
    f = two_org_fixture
    organization_service.deactivate_organization(session, f["org_a"].id)
    decision = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=f["org_a"].id, permission="organization.view"
    )
    assert decision.allowed is False
    assert decision.reason == "organization_inactive"


def test_manipulated_organization_id_denied(session, two_org_fixture) -> None:
    """Supplying a syntactically valid but nonexistent organization_id
    must not grant access."""
    f = two_org_fixture
    decision = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=999999, permission="organization.view"
    )
    assert decision.allowed is False
    assert decision.reason == "organization_not_found"


# ---------------------------------------------------------------------------
# Future Jarvis authorization tests (spec section 10) — authorization-service
# only. jarvis_tools.py / jarvis_context.py / specialist_orchestration.py are
# untouched by Phase 1.
# ---------------------------------------------------------------------------

def test_receptionist_cannot_use_finance_sensitive_jarvis_tools(session, two_org_fixture) -> None:
    f = two_org_fixture
    decision = authorization_service.authorize(
        session, user_id=f["receptionist_a"].id, organization_id=f["org_a"].id, permission="jarvis.finance"
    )
    assert decision.allowed is False
    assert decision.reason == "permission_denied"


def test_owner_can_use_finance_sensitive_jarvis_tools(session, two_org_fixture) -> None:
    f = two_org_fixture
    decision = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=f["org_a"].id, permission="jarvis.finance"
    )
    assert decision.allowed is True


def test_user_a_denied_jarvis_under_organization_b(session, two_org_fixture) -> None:
    f = two_org_fixture
    decision = authorization_service.authorize(
        session, user_id=f["owner_a"].id, organization_id=f["org_b"].id, permission="jarvis.use"
    )
    assert decision.allowed is False
    assert decision.reason == "membership_not_found"


def test_practitioner_gets_operations_jarvis_but_not_marketing(session, two_org_fixture) -> None:
    f = two_org_fixture
    allowed = authorization_service.authorize(
        session, user_id=f["practitioner_a"].id, organization_id=f["org_a"].id, permission="jarvis.operations"
    )
    denied = authorization_service.authorize(
        session, user_id=f["practitioner_a"].id, organization_id=f["org_a"].id, permission="jarvis.marketing"
    )
    assert allowed.allowed is True
    assert denied.allowed is False


# ---------------------------------------------------------------------------
# Organization-context resolution (AuthenticatedIdentity)
# ---------------------------------------------------------------------------

def test_resolve_identity_returns_full_context(session, two_org_fixture) -> None:
    f = two_org_fixture
    decision = authorization_service.resolve_identity(
        session, user_id=f["owner_a"].id, organization_id=f["org_a"].id
    )
    assert decision.allowed is True
    identity = decision.identity
    assert identity is not None
    assert identity.user_id == f["owner_a"].id
    assert identity.organization_id == f["org_a"].id
    assert identity.role == MembershipRole.OWNER
    assert identity.has_permission("finance.view") is True
    assert identity.has_permission("nonexistent.permission") is False


# ---------------------------------------------------------------------------
# Authentication service foundation
# ---------------------------------------------------------------------------

def test_authenticate_success_returns_active_memberships(session, two_org_fixture) -> None:
    f = two_org_fixture
    result = authentication_service.authenticate(session, email="owner-a@example.com", password="pw-owner-a-123")
    assert result.success is True
    assert result.user is not None
    assert result.user.id == f["owner_a"].id
    assert len(result.memberships) == 1
    assert result.memberships[0].organization_id == f["org_a"].id


def test_authenticate_wrong_password_fails(session, two_org_fixture) -> None:
    result = authentication_service.authenticate(session, email="owner-a@example.com", password="wrong-password")
    assert result.success is False
    assert result.reason == "invalid_credentials"


def test_authenticate_unknown_email_fails(session) -> None:
    result = authentication_service.authenticate(session, email="nobody@example.com", password="whatever")
    assert result.success is False
    assert result.reason == "user_not_found"


def test_authenticate_sets_last_login_at(session, two_org_fixture) -> None:
    f = two_org_fixture
    assert f["owner_a"].last_login_at is None
    authentication_service.authenticate(session, email="owner-a@example.com", password="pw-owner-a-123")
    assert f["owner_a"].last_login_at is not None


# ---------------------------------------------------------------------------
# Bootstrap script — idempotency and password-preservation, run as a real
# subprocess against a private temp SQLite file (same technique
# test_phase0_schema.py uses for `alembic upgrade head`).
# ---------------------------------------------------------------------------

def _run_bootstrap(db_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", ""), "DATABASE_URL": f"sqlite:///{db_path}"}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return subprocess.run(
        [sys.executable, "scripts/bootstrap_identity.py", *extra_args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_bootstrap_script_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bootstrap_test.db"

        env = {"PATH": os.environ.get("PATH", ""), "DATABASE_URL": f"sqlite:///{db_path}"}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        assert upgrade.returncode == 0, upgrade.stderr

        args = [
            "--org-slug", "test-clinic", "--org-name", "Test Clinic",
            "--owner-email", "owner@test-clinic.example", "--owner-password", "first-password-123",
        ]
        first = _run_bootstrap(db_path, *args)
        assert first.returncode == 0, first.stderr
        assert "Created Organization" in first.stdout
        assert "Created User" in first.stdout
        assert "Created Membership" in first.stdout

        second = _run_bootstrap(db_path, *args)
        assert second.returncode == 0, second.stderr
        assert "already exists" in second.stdout
        assert "No change made" in second.stdout


def test_bootstrap_never_silently_overwrites_credentials() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bootstrap_pw_test.db"
        env = {"PATH": os.environ.get("PATH", ""), "DATABASE_URL": f"sqlite:///{db_path}"}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
        )

        first = _run_bootstrap(
            db_path, "--org-slug", "clinic", "--org-name", "Clinic",
            "--owner-email", "owner@clinic.example", "--owner-password", "original-password-1",
        )
        assert first.returncode == 0, first.stderr

        second = _run_bootstrap(
            db_path, "--org-slug", "clinic", "--org-name", "Clinic",
            "--owner-email", "owner@clinic.example", "--owner-password", "a-totally-different-password-2",
        )
        assert second.returncode == 0, second.stderr
        assert "Password left untouched" in second.stdout

        # Verify directly against the resulting database: the original
        # password still authenticates, the second one does not.
        eng = make_engine(f"sqlite:///{db_path}")
        try:
            with Session(eng) as verify_session:
                ok = authentication_service.authenticate(
                    verify_session, email="owner@clinic.example", password="original-password-1"
                )
                bad = authentication_service.authenticate(
                    verify_session, email="owner@clinic.example", password="a-totally-different-password-2"
                )
                assert ok.success is True
                assert bad.success is False
                assert bad.reason == "invalid_credentials"
        finally:
            eng.dispose()
