"""Encryption at rest for user-supplied broker credentials.

    AES-256-GCM (authenticated encryption), a fresh 96-bit nonce per value.
    The key comes from the environment (CREDENTIALS_ENCRYPTION_KEY) or a file
    (CREDENTIALS_ENCRYPTION_KEY_FILE, e.g. a Docker secret) and never from the
    database: the encrypted values live in Postgres, the key does not.

    Every ciphertext is bound to its owner and purpose through the associated
    data ("<user id>|<broker>|<field>"), so a value copied to another user's
    row, or from the secret column to the key column, fails to decrypt.

    Stored form:  "v1:<key id>:<base64(nonce + ciphertext + tag)>"
    The key id (first 8 hex chars of SHA-256 of the key) allows rotation:
    CREDENTIALS_ENCRYPTION_PREVIOUS_KEYS still decrypt; new values always use
    the current key.

There is deliberately no plaintext fallback: without a key the vault is
unavailable and credentials cannot be saved.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "v1"


class VaultUnavailable(RuntimeError):
    """No usable encryption key is configured."""


class VaultError(RuntimeError):
    """A stored value could not be decrypted (wrong key, tampered, moved)."""


def generate_key() -> str:
    """A new random key in the form the settings expect (urlsafe base64)."""

    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _decode_key(text: str) -> bytes:
    text = text.strip()
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise VaultUnavailable("the credentials encryption key is not valid base64") from exc
    if len(raw) != 32:
        raise VaultUnavailable("the credentials encryption key must be 32 bytes (base64)")
    return raw


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


@dataclass(frozen=True)
class _Key:
    kid: str
    aead: AESGCM


class CredentialVault:
    def __init__(self, current: str, previous: list[str] | None = None) -> None:
        key = _decode_key(current)
        self._current = _Key(_key_id(key), AESGCM(key))
        self._keys = {self._current.kid: self._current}
        for text in previous or []:
            old = _decode_key(text)
            self._keys.setdefault(_key_id(old), _Key(_key_id(old), AESGCM(old)))

    def __repr__(self) -> str:  # never the key
        return f"CredentialVault(key_id={self._current.kid}, keys={len(self._keys)})"

    @property
    def key_id(self) -> str:
        return self._current.kid

    @staticmethod
    def context(owner: str, broker: str, field: str) -> bytes:
        return f"{owner}|{broker}|{field}".encode()

    def encrypt(self, plaintext: str, *, context: bytes) -> str:
        nonce = os.urandom(12)
        sealed = self._current.aead.encrypt(nonce, plaintext.encode(), context)
        return f"{PREFIX}:{self._current.kid}:{base64.b64encode(nonce + sealed).decode()}"

    def decrypt(self, stored: str, *, context: bytes) -> str:
        try:
            prefix, kid, body = stored.split(":", 2)
        except ValueError as exc:
            raise VaultError("not an encrypted value") from exc
        key = self._keys.get(kid)
        if prefix != PREFIX or key is None:
            raise VaultError("encrypted with an unknown key")
        try:
            raw = base64.b64decode(body)
            return key.aead.decrypt(raw[:12], raw[12:], context).decode()
        except (InvalidTag, ValueError, binascii.Error) as exc:
            raise VaultError("the value could not be decrypted") from exc

    def needs_rotation(self, stored: str) -> bool:
        return not stored.startswith(f"{PREFIX}:{self._current.kid}:")


def vault_from_settings(settings: object) -> CredentialVault | None:
    """The configured vault, or None when no key is set (then credentials
    cannot be stored). A key that is set but invalid raises VaultUnavailable."""

    secret = getattr(settings, "credentials_encryption_key", None)
    text = secret.get_secret_value() if secret is not None else ""
    path = getattr(settings, "credentials_encryption_key_file", None)
    if not text and path:
        try:
            text = Path(path).read_text().strip()
        except OSError as exc:
            raise VaultUnavailable(f"cannot read CREDENTIALS_ENCRYPTION_KEY_FILE: {exc}") from exc
    if not text:
        return None
    previous_raw = getattr(settings, "credentials_encryption_previous_keys", None)
    previous_text = previous_raw.get_secret_value() if previous_raw is not None else ""
    previous = [p for p in previous_text.split(",") if p.strip()]
    return CredentialVault(text, previous)


__all__ = [
    "CredentialVault",
    "VaultError",
    "VaultUnavailable",
    "generate_key",
    "vault_from_settings",
]
