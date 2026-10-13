"""Datetime helpers (UTC everywhere)."""
from __future__ import annotations

from typing import Optional

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC datetime (matches SQLAlchemy DateTime defaults)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: Optional[datetime]) -> Optional[str]:
    """ISO-8601 string or None."""
    return dt.isoformat() if dt else None
