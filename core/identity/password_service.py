"""Password hashing — Argon2id via argon2-cffi.

Not used by core/auth.py (the live login path, which authenticates
against shared deployment credentials — see that file's own docstring).
This is a standalone piece of Phase 1's future identity system.

No custom cryptography: argon2-cffi's PasswordHasher handles salting,
parameterization, and encoding entirely; this module is a thin wrapper
that never touches raw hash bytes.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Returns an Argon2id hash string (includes a random salt and all
    parameters needed to verify it later — nothing else needs to be
    stored alongside it)."""
    if not plaintext:
        raise ValueError("password must not be empty")
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Never raises on a wrong password or a malformed/foreign hash —
    both simply return False, so callers can treat this as a plain
    boolean check without needing their own try/except."""
    if not plaintext or not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, plaintext)
    except (VerifyMismatchError, InvalidHash):
        return False
