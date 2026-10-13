"""Admin schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class AdminUserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    created_at: Optional[datetime] = None
    job_count: int = 0

    model_config = {"from_attributes": True}


class AdminUserUpdate(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(ADMIN|USER)$")
    is_active: Optional[bool] = None
    full_name: Optional[str] = Field(default=None, max_length=255)


class AdminJobOut(BaseModel):
    id: str
    user_email: str
    query_raw: str
    status: str
    progress: int
    total: int
    emails_found: int
    provider: str
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class UsageSummary(BaseModel):
    today_requests: int
    today_cost: float
    month_requests: int
    month_cost: float
    by_provider: list[dict]


class ProviderConfigOut(BaseModel):
    name: str
    enabled: bool
    has_api_key: bool
    config_json: str


class ProviderConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    api_key: Optional[str] = Field(default=None, max_length=512)
    config_json: Optional[str] = Field(default=None, max_length=4096)
