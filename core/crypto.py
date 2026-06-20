"""Symmetric encryption for connector tokens at rest (plan §3, §10).

Tokens are NEVER stored or logged in plaintext. We derive a Fernet key from
TOKEN_ENCRYPTION_KEY (a 64-hex-char / 32-byte secret) and encrypt the token JSON blob.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache

from cryptography.fernet import Fernet

from config import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key_hex = get_settings().token_encryption_key
    if not key_hex:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set; cannot encrypt connector tokens")
    raw = bytes.fromhex(key_hex)
    if len(raw) != 32:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be 32 bytes (64 hex chars)")
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_json(data: dict[str, object]) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode("utf-8"))


def decrypt_json(blob: bytes) -> dict[str, object]:
    decoded: dict[str, object] = json.loads(_fernet().decrypt(blob).decode("utf-8"))
    return decoded
