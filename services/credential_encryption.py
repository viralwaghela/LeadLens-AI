"""Phase 6 — application-level encryption for organization integration
secrets (WhatsApp access tokens, Gmail/Calendar service-account keys).

Uses `cryptography`'s Fernet (AES-128-CBC + HMAC-SHA256, authenticated
symmetric encryption) — a vetted, widely-used primitive, not a custom
scheme. The master key is read only from the environment
(LEADLENS_CREDENTIAL_ENCRYPTION_KEY) and is never written to the
database; each OrganizationIntegration row stores only the ciphertext
plus which key version encrypted it.

Key-rotation readiness (Phase 6 spec section 3 — not a full KMS): the
active key is "version 1" unless LEADLENS_CREDENTIAL_ENCRYPTION_KEY_VERSION
overrides it. A future rotation adds LEADLENS_CREDENTIAL_ENCRYPTION_KEY_V<N>
env vars for old key versions (so old rows can still be decrypted with
the key that actually encrypted them) and bumps
LEADLENS_CREDENTIAL_ENCRYPTION_KEY / LEADLENS_CREDENTIAL_ENCRYPTION_KEY_VERSION
to the new active key; re-encrypting existing rows under the new key is
a deliberate future migration script, not built here (would be
over-engineering key management for a single-clinic-scale deployment
today).

Nothing in this module ever logs a plaintext secret, a ciphertext blob,
or the master key itself — only key *versions* (small integers) and
error *categories* are ever placed in logs/audit metadata.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_LOG = logging.getLogger(__name__)

ENCRYPTION_FORMAT_VERSION = 1


class CredentialEncryptionError(Exception):
    """Base class for every encryption/decryption failure in this
    module. Messages are always safe to log — never include the
    plaintext, ciphertext, or key material."""


class EncryptionKeyUnavailable(CredentialEncryptionError):
    """The master key (or the specific version needed) is missing or
    malformed. Raised for both 'never configured' and 'wrong/rotated-away
    key' cases — callers must fail closed, never fall back to a
    different organization's data."""


class CredentialDecryptionError(CredentialEncryptionError):
    """Ciphertext could not be decrypted with the resolved key —
    corrupted payload, truncated data, or (extremely unlikely with
    Fernet's authentication) tampering. Never treat this the same as
    'not configured' — those are different failure categories and must
    be distinguishable in audit metadata."""


def _env_key_for_version(version: int) -> str:
    if version == 1:
        return "LEADLENS_CREDENTIAL_ENCRYPTION_KEY"
    return f"LEADLENS_CREDENTIAL_ENCRYPTION_KEY_V{version}"


def active_key_version() -> int:
    raw = os.getenv("LEADLENS_CREDENTIAL_ENCRYPTION_KEY_VERSION", "1").strip()
    try:
        return int(raw)
    except ValueError:
        return 1


@lru_cache(maxsize=8)
def _fernet_for_version(version: int) -> Fernet:
    """Cached per key *version* (an int, never the key material itself
    as a cache key) — cheap to call repeatedly. Raises
    EncryptionKeyUnavailable if the env var for this version is missing
    or not a valid Fernet key; never returns a usable Fernet built from
    bad input."""
    env_name = _env_key_for_version(version)
    raw_key = os.getenv(env_name, "").strip()
    if not raw_key:
        raise EncryptionKeyUnavailable(
            f"Encryption key version {version} ({env_name}) is not configured."
        )
    try:
        return Fernet(raw_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise EncryptionKeyUnavailable(
            f"Encryption key version {version} ({env_name}) is not a valid Fernet key."
        ) from exc


def encrypt_credential_fields(fields: dict[str, Any]) -> tuple[str, int]:
    """Encrypts a dict of secret fields (e.g. {"access_token": "..."})
    as one JSON blob. Returns (ciphertext, key_version_used) — the
    caller stores both on the OrganizationIntegration row."""
    version = active_key_version()
    fernet = _fernet_for_version(version)
    plaintext = json.dumps(fields, ensure_ascii=False, default=str).encode("utf-8")
    ciphertext = fernet.encrypt(plaintext).decode("utf-8")
    return ciphertext, version


def decrypt_credential_fields(ciphertext: str, key_version: int) -> dict[str, Any]:
    """Raises EncryptionKeyUnavailable if the key for `key_version` isn't
    configured, or CredentialDecryptionError if the ciphertext doesn't
    decrypt/parse under that key. Never returns partial or guessed
    data — decryption either fully succeeds or raises."""
    fernet = _fernet_for_version(key_version)
    try:
        plaintext = fernet.decrypt(ciphertext.encode("utf-8"))
    except (InvalidToken, ValueError) as exc:
        raise CredentialDecryptionError(
            "Stored credential ciphertext could not be decrypted with its recorded key version."
        ) from exc
    try:
        data = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialDecryptionError(
            "Decrypted credential payload was not valid JSON."
        ) from exc
    if not isinstance(data, dict):
        raise CredentialDecryptionError("Decrypted credential payload was not an object.")
    return data


_SECRET_FIELD_NAMES = {
    "access_token", "refresh_token", "token", "api_key", "private_key",
    "service_account_json", "service_account_key", "client_secret",
    "password", "encryption_key",
}


def redact(value: Any) -> Any:
    """Best-effort defensive redaction for anything that might end up in
    a log line, exception message, or admin-facing response. Not the
    primary safety mechanism (callers should simply never pass secrets
    into logs/exceptions in the first place — see
    services/integration_credentials.py) but a second layer in case a
    dict containing secret-shaped keys is ever stringified by mistake."""
    if isinstance(value, dict):
        return {
            key: ("***REDACTED***" if str(key).lower() in _SECRET_FIELD_NAMES else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
