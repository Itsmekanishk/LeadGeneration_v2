"""Auth schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class SupabaseLoginRequest(BaseModel):
    access_token: str = Field(min_length=1, description="Supabase session access token")
    id_token: Optional[str] = None
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(default="", max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserOut"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    # Only present in non-production without SMTP, so the flow is testable.
    dev_token: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


TokenResponse.model_rebuild()
