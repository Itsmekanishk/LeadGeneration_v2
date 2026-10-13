"""Authentication & account endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DBSession

from app.core.deps import CurrentUser, bearer_scheme
from app.core.rate_limiter import check_rate_limit, RateLimit
from app.models.database import get_db
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SupabaseLoginRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth_service
from app.services.auth_service import AuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "anonymous"


def _loopback_exempt(request: Request) -> bool:
    """Dev/load-test opt-out (RATE_LIMIT_DISABLE_LOOPBACK=true): skip per-IP
    auth limits for loopback clients so local scripts aren't throttled.
    Default is OFF — production limits always apply."""
    import os

    if os.environ.get("RATE_LIMIT_DISABLE_LOOPBACK", "false") != "true":
        return False
    return _client_ip(request) in ("127.0.0.1", "::1", "testclient")


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request,
             db: DBSession = Depends(get_db)) -> TokenResponse:
    """Create an account and receive tokens (first user becomes ADMIN)."""
    if not _loopback_exempt(request):
        allowed, retry = check_rate_limit(
            f"register:{_client_ip(request)}",
            RateLimit("register", 5, 3600),
        )
        if not allowed:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="Too many registrations; try later",
                                headers={"Retry-After": str(retry)})
    try:
        user = auth_service.create_user(
            db, email=body.email, password=body.password, full_name=body.full_name
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    access = auth_service.login_tokens(db, user)  # type: ignore[attr-defined]
    return access


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request,
          db: DBSession = Depends(get_db)) -> TokenResponse:
    """Authenticate with email+password; returns access+refresh tokens."""
    if not _loopback_exempt(request):
        allowed, retry = check_rate_limit(
            f"login:{_client_ip(request)}", RateLimit("login", 10, 300)
        )
        if not allowed:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="Too many attempts",
                                headers={"Retry-After": str(retry)})
    try:
        user = auth_service.authenticate(db, body.email, body.password)
    except AuthError:
        # same message for unknown user and wrong password (no enumeration)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    auth_service.audit(db, user_id=user.id, action="login", ip=_client_ip(request))
    access = auth_service.login_tokens(db, user)  # type: ignore[attr-defined]
    db.commit()
    return access


@router.post("/supabase-login", response_model=TokenResponse)
def supabase_login(
    body: SupabaseLoginRequest,
    request: Request,
    db: DBSession = Depends(get_db),
) -> TokenResponse:
    """Exchange a verified Supabase access token (Gmail/Google OAuth) for application session tokens."""
    if not _loopback_exempt(request):
        allowed, retry = check_rate_limit(
            f"supabase-login:{_client_ip(request)}", RateLimit("supabase-login", 15, 300)
        )
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts",
                headers={"Retry-After": str(retry)},
            )
    try:
        claims = auth_service.verify_supabase_token(
            body.access_token,
            fallback_email=body.email,
            fallback_name=body.full_name,
        )
        email = claims.get("email")
        if not email:
            raise AuthError("No email associated with Supabase user")
        full_name = claims.get("full_name") or body.full_name or ""
        tokens = auth_service.login_or_register_supabase_user(
            db, email=email, full_name=full_name
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    auth_service.audit(
        db,
        user_id=tokens.user.id,
        action="supabase_login",
        ip=_client_ip(request),
        detail={"email": tokens.user.email, "provider": "supabase_google"},
    )
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: DBSession = Depends(get_db)) -> TokenResponse:
    """Rotate the refresh token and mint a new access token."""
    try:
        user, new_refresh = auth_service.rotate_refresh_token(db, body.refresh_token)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    from app.core.security import create_access_token

    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=new_refresh,
        user=UserOut.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest,
           credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
           db: DBSession = Depends(get_db)) -> None:
    """Revoke the presented refresh token."""
    try:
        auth_service.revoke_refresh_token(db, body.refresh_token)
    except AuthError:
        pass  # logout is idempotent


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(body: ForgotPasswordRequest, request: Request,
                    db: DBSession = Depends(get_db)) -> ForgotPasswordResponse:
    """Begin password reset. Response never reveals account existence."""
    allowed, retry = check_rate_limit(
        f"forgot:{_client_ip(request)}", RateLimit("forgot", 5, 900)
    )
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many requests", headers={"Retry-After": str(retry)})
    token = auth_service.request_password_reset(db, body.email)
    from app.core.config import get_settings

    dev_token = token if (token and not get_settings().is_production) else None
    return ForgotPasswordResponse(
        message="If the account exists, a reset link has been sent.",
        dev_token=dev_token,
    )


@router.post("/reset-password", response_model=UserOut)
def reset_password(body: ResetPasswordRequest,
                   db: DBSession = Depends(get_db)) -> UserOut:
    """Complete password reset with a valid token."""
    try:
        user = auth_service.consume_password_reset_token(db, body.token, body.new_password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return UserOut.model_validate(user)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """Return the authenticated user's profile."""
    return UserOut.model_validate(user)
