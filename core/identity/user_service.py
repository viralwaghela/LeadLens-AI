"""User CRUD for the Phase 1 identity layer.

Not used by core/auth.py. See core/identity/authentication_service.py
for the service that actually verifies a login attempt against users
created here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db.models.identity import User, UserStatus
from core.db.models.identity_audit import IdentityEventType
from core.identity import audit_service
from core.identity.password_service import hash_password


class DuplicateUserError(Exception):
    """Raised when a user with the given email already exists."""


class UserNotFoundError(Exception):
    """Raised when a lookup-by-id finds nothing."""


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    email_norm = _normalize_email(email)
    existing = session.scalar(select(User).where(User.email == email_norm))
    if existing is not None:
        raise DuplicateUserError(f"a user with email {email_norm!r} already exists")

    user = User(email=email_norm, password_hash=hash_password(password), status=status)
    session.add(user)
    session.flush()

    audit_service.record_event(
        session,
        event_type=IdentityEventType.USER_CREATED,
        target_user_id=user.id,
        detail={"email": email_norm, "status": status.value},
    )
    return user


def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == _normalize_email(email)))


def disable_user(session: Session, user_id: int, *, actor_user_id: int | None = None) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError(f"no user with id {user_id!r}")
    user.status = UserStatus.DISABLED
    session.flush()
    audit_service.record_event(
        session,
        event_type=IdentityEventType.USER_DISABLED,
        actor_user_id=actor_user_id,
        target_user_id=user.id,
    )
    return user


def activate_user(session: Session, user_id: int, *, actor_user_id: int | None = None) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError(f"no user with id {user_id!r}")
    user.status = UserStatus.ACTIVE
    session.flush()
    audit_service.record_event(
        session,
        event_type=IdentityEventType.USER_ACTIVATED,
        actor_user_id=actor_user_id,
        target_user_id=user.id,
    )
    return user


def record_login(session: Session, user_id: int) -> User:
    """Called by authentication_service.authenticate() on a successful
    login. Not an audit event on its own — last_login_at on the user row
    is the record."""
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError(f"no user with id {user_id!r}")
    user.last_login_at = datetime.now(timezone.utc)
    session.flush()
    return user
