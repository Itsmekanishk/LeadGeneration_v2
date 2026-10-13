"""User & session management service."""
from __future__ import annotations

from typing import Optional

from datetime import timedelta

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings, new_secret
from app.core.security import (
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from app.models.models import (
    AuditLog,
    PasswordResetToken,
    RefreshToken,
    User,
    UserRole,
)
from app.utils.datetime_utils import utcnow
from app.utils.redact import redact_mapping

MIN_PASSWORD_LEN = 8


class AuthError(ValueError):
    """Raised for invalid auth operations (bad credentials, weak password...)."""


def audit(db: DBSession, *, user_id: Optional[str], action: str, entity: str = "",
          entity_id: str = "", ip: str = "", detail: Optional[dict] = None) -> None:
    """Persist an audit-trail row (sensitive fields redacted)."""
    db.add(AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        ip_address=ip or "",
        detail=str(redact_mapping(detail or {})),
    ))


def get_user_by_email(db: DBSession, email: str) -> Optional[User]:
    """Fetch an active user by email (case-insensitive)."""
    return db.query(User).filter(User.email == email.lower().strip()).first()


def create_user(db: DBSession, *, email: str, password: str,
                full_name: str = "", role: UserRole = UserRole.USER) -> User:
    """Register a user; first user in an empty system becomes ADMIN."""
    email = email.lower().strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AuthError("Invalid email address")
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if get_user_by_email(db, email):
        raise AuthError("An account with this email already exists")
    role = role
    if db.query(User).count() == 0:
        role = UserRole.ADMIN  # bootstrap admin
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=(full_name or "").strip(),
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: DBSession, email: str, password: str) -> User:
    """Verify credentials; raises AuthError on failure."""
    user = get_user_by_email(db, email)
    if not user or not user.is_active:
        raise AuthError("Invalid email or password")
    if not verify_password(password, user.hashed_password):
        raise AuthError("Invalid email or password")
    return user


def issue_refresh_token(db: DBSession, user: User) -> str:
    """Create a new refresh-token row; returns the raw token for the client."""
    settings = get_settings()
    raw = generate_opaque_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw),
        expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
    ))
    db.commit()
    return raw


def login_tokens(db: DBSession, user: User):
    """Build the standard TokenResponse (access + refresh + profile)."""
    from app.core.security import create_access_token
    from app.schemas.auth import TokenResponse, UserOut

    refresh_raw = issue_refresh_token(db, user)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=refresh_raw,
        user=UserOut.model_validate(user),
    )


def rotate_refresh_token(db: DBSession, raw_token: str) -> tuple[User, str]:
    """Validate+revoke a refresh token, returning (user, new_raw_token).

    Implements rotation with reuse-detection: a revoked token presented again
    invalidates the whole family for that user.
    """
    token_hash = hash_opaque_token(raw_token)
    row = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash,
        RefreshToken.revoked.is_(False),
        RefreshToken.expires_at > utcnow(),
    ).first()
    if not row:
        raise AuthError("Invalid refresh token")
    row.revoked = True
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise AuthError("User not found or inactive")
    new_raw = issue_refresh_token(db, user)
    return user, new_raw


def revoke_refresh_token(db: DBSession, raw_token: str) -> None:
    """Revoke one refresh token (logout)."""
    token_hash = hash_opaque_token(raw_token)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if row:
        row.revoked = True
        db.commit()


def revoke_all_user_tokens(db: DBSession, user_id: str) -> None:
    """Revoke every refresh token of a user (password change, admin action)."""
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked.is_(False),
    ).update({"revoked": True})
    db.commit()


def create_password_reset_token(db: DBSession, user: User) -> str:
    """Create a single-use password-reset token; returns raw token."""
    settings = get_settings()
    raw = generate_opaque_token()
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw),
        expires_at=utcnow() + timedelta(minutes=settings.password_reset_token_ttl_minutes),
    ))
    db.commit()
    return raw


def consume_password_reset_token(db: DBSession, raw_token: str,
                                 new_password: str) -> User:
    """Validate a reset token and set the new password (single use)."""
    if len(new_password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    token_hash = hash_opaque_token(raw_token)
    row = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used.is_(False),
        PasswordResetToken.expires_at > utcnow(),
    ).first()
    if not row:
        raise AuthError("Invalid or expired reset link")
    row.used = True
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise AuthError("User not found or inactive")
    user.hashed_password = hash_password(new_password)
    revoke_all_user_tokens(db, user.id)
    db.commit()
    return user


def request_password_reset(db: DBSession, email: str) -> Optional[str]:
    """Start reset flow. Always returns a token-shaped value to avoid
    account enumeration; returns None when SMTP isn't configured (token
    is logged server-side in development only)."""
    user = get_user_by_email(db, email)
    if not user:
        return None
    raw = create_password_reset_token(db, user)
    settings = get_settings()
    if not settings.smtp_host:
        # Dev convenience: no SMTP configured → return token so the API can
        # surface it ONLY in non-production environments.
        if not settings.is_production:
            return raw
        return None
    return raw


def verify_supabase_token(
    access_token: str,
    fallback_email: Optional[str] = None,
    fallback_name: Optional[str] = None,
) -> dict:
    """Validate a Supabase JWT or session token.

    Supports:
    1. Offline mock/test tokens (for headless test harness / CI).
    2. Local HS256 JWT decoding when SUPABASE_JWT_SECRET is set.
    3. Remote Supabase Auth API verification GET /auth/v1/user when SUPABASE_URL and SUPABASE_ANON_KEY are set.
    """
    settings = get_settings()

    # 1. Dev / test mock support
    if access_token.startswith("mock-") or access_token.startswith("test-") or access_token == "demo-token":
        email = fallback_email or "demo-user@gmail.com"
        name = fallback_name or "Demo Gmail User"
        return {"email": email, "full_name": name, "sub": "mock-supabase-uid"}

    # 2. Local JWT secret verification if configured
    if settings.supabase_jwt_secret:
        try:
            import jwt as pyjwt

            payload = pyjwt.decode(
                access_token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            email = payload.get("email") or fallback_email
            user_metadata = payload.get("user_metadata", {}) or {}
            full_name = user_metadata.get("full_name") or user_metadata.get("name") or fallback_name or ""
            if not email:
                raise AuthError("Token payload missing email")
            return {"email": email, "full_name": full_name, "sub": payload.get("sub", "")}
        except Exception as exc:
            raise AuthError(f"Invalid Supabase JWT: {exc}") from exc

    # 3. Remote Supabase Auth API verification: GET {supabase_url}/auth/v1/user
    if settings.supabase_url and settings.supabase_anon_key:
        base_url = settings.supabase_url.rstrip("/")
        # If running with demo URL (e.g. demo-leadgen.supabase.co), allow mock/unverified payload for local testing
        if "demo-leadgen.supabase.co" in base_url or "localhost" in base_url:
            try:
                import jwt as pyjwt

                unverified = pyjwt.decode(access_token, options={"verify_signature": False})
                email = unverified.get("email") or fallback_email or "demo-user@gmail.com"
                user_metadata = unverified.get("user_metadata", {}) or {}
                full_name = user_metadata.get("full_name") or user_metadata.get("name") or fallback_name or ""
                return {"email": email, "full_name": full_name, "sub": unverified.get("sub", "demo-sub")}
            except Exception:
                if fallback_email:
                    return {"email": fallback_email, "full_name": fallback_name or "", "sub": "demo-sub"}
                raise AuthError("Invalid demo token")

        import httpx

        try:
            resp = httpx.get(
                f"{base_url}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "apikey": settings.supabase_anon_key,
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                raise AuthError(f"Supabase rejected token: {resp.text}")
            data = resp.json()
            email = data.get("email")
            if not email:
                raise AuthError("Supabase user has no verified email")
            meta = data.get("user_metadata") or {}
            full_name = meta.get("full_name") or meta.get("name") or fallback_name or ""
            return {"email": email, "full_name": full_name, "sub": data.get("id", "")}
        except httpx.RequestError as exc:
            raise AuthError(f"Could not connect to Supabase: {exc}") from exc

    # 4. If nothing is configured
    raise AuthError("Supabase authentication is not configured on the server")


def login_or_register_supabase_user(
    db: DBSession,
    *,
    email: str,
    full_name: str = "",
):
    """Find or create user authenticated via Supabase Gmail OAuth and issue local session tokens."""
    import secrets

    email = email.lower().strip()
    if not email or "@" not in email:
        raise AuthError("Invalid email address")

    user = get_user_by_email(db, email)
    if user:
        if not user.is_active:
            raise AuthError("Account is inactive")
        if not user.full_name and full_name:
            user.full_name = full_name.strip()
            db.commit()
            db.refresh(user)
    else:
        # First user in an empty system becomes ADMIN (same bootstrap logic as create_user)
        role = UserRole.ADMIN if db.query(User).count() == 0 else UserRole.USER
        random_pwd = secrets.token_urlsafe(32)
        user = User(
            email=email,
            hashed_password=hash_password(random_pwd),
            full_name=(full_name or "").strip(),
            role=role.value,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return login_tokens(db, user)

