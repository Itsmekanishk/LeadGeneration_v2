"""Common schemas: health, errors."""
from __future__ import annotations

from pydantic import BaseModel


class HealthOut(BaseModel):
    status: str
    app_env: str
    checks: dict[str, str]


class ErrorOut(BaseModel):
    detail: str
