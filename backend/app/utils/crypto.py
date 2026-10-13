"""Symmetric encryption helpers (provider API keys at rest)."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key)


def encrypt(value: str) -> str:
    """Encrypt a plaintext secret; returns urlsafe token string."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token; raises InvalidToken on wrong key/tampering."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Cannot decrypt stored secret (key mismatch?)") from exc
