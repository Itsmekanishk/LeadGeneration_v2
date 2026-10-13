"""Password hashing and token security primitives.

- Passwords: Argon2 (passlib) when available, otherwise bcrypt. The chosen
  scheme is recorded in the hash itself, so verifying is scheme-agnostic.
- Access tokens: short-lived JWTs (HS256, SECRET_KEY from env).
- Opaque tokens (refresh / password reset): random 32-byte urlsafe strings;
  only a SHA-256 hash is stored in the database.
"""
from __future__ import annotations

from typing import Optional

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings

logger = logging.getLogger("locallead.security")

_Schemes = None
_pwd_context = None


def _get_pwd_context():
    """Lazily build the passlib CryptContext (Argon2 preferred, bcrypt fallback)."""
    global _Schemes, _pwd_context  # noqa: PLW0603
    if _pwd_context is None:
        try:
            from passlib.context import CryptContext as _C  # type: ignore
        except Exception:  # pragma: no cover - defensive
            raise RuntimeError("passlib is required for password hashing")
        schemes = []
        try:
            import argon2  # noqa: F401  # type: ignore

            schemes.append("argon2")
        except Exception:
            logger.warning("argon2 not installed; falling back to bcrypt")
        try:
            import bcrypt  # noqa: F401  # type: ignore

            schemes.append("bcrypt")
        except Exception:
            pass
        if not schemes:
            raise RuntimeError("Neither argon2 nor bcrypt is installed")
        _pwd_context = _C(schemes=schemes, deprecated="auto")
    return _pwd_context


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2/bcrypt."""
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    return _get_pwd_context().hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password verification."""
    if not plain or not hashed:
        return False
    try:
        return _get_pwd_context().verify(plain, hashed)
    except Exception:
        return False


def create_access_token(subject: str, role: str, extra: Optional[dict] = None) -> str:
    """Create a signed short-lived JWT access token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
        "jti": hashlib.sha256(f"{subject}{now.timestamp()}".encode()).hexdigest()[:16],
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Decode and validate an access token; raises jwt exceptions on failure."""
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def generate_opaque_token() -> str:
    """Random urlsafe token returned to clients (never stored raw)."""
    import secrets

    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    """SHA-256 hex digest used for storing opaque tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


ALGORITHM = "HS256"
