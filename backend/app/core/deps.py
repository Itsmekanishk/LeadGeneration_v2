"""FastAPI dependencies for authentication and authorization."""
from __future__ import annotations

from typing import Optional, Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.models.database import get_db
from app.models.models import User

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


def _extract_token(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> str:
    if credentials and credentials.credentials:
        return credentials.credentials
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: DBSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user from the Bearer token."""
    token = _extract_token(credentials)
    try:
        payload = decode_access_token(token)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        ) from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    request.state.user_id = user.id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    """Guard dependency: 403 unless the user is an ADMIN."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]
