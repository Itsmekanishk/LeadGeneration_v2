"""Redact sensitive fields before persisting audit payloads."""
from __future__ import annotations

_SENSITIVE = {"password", "new_password", "token", "refresh_token", "api_key", "secret"}


def redact_mapping(data: dict) -> dict:
    """Return a copy of *data* with sensitive values masked."""
    out = {}
    for key, value in (data or {}).items():
        if key.lower() in _SENSITIVE:
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = redact_mapping(value)
        else:
            out[key] = value
    return out
