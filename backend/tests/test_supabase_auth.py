"""Tests for Supabase Gmail/Google OAuth authentication."""
from __future__ import annotations

import jwt
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.database import session_scope
from app.models.models import User, UserRole


def test_supabase_login_new_user(client: TestClient):
    """A new user signing in with Supabase Gmail is registered and receives session tokens."""
    payload = {
        "access_token": "mock-supabase-token-1",
        "email": "new.lead.user@gmail.com",
        "full_name": "New Gmail Lead",
    }
    res = client.post("/api/auth/supabase-login", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "new.lead.user@gmail.com"
    assert data["user"]["full_name"] == "New Gmail Lead"

    # Verify user exists in DB
    with session_scope() as db:
        db_user = db.query(User).filter(User.email == "new.lead.user@gmail.com").first()
        assert db_user is not None
        assert db_user.full_name == "New Gmail Lead"

    # Verify the returned access token can authenticate against /api/auth/me
    me_res = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "new.lead.user@gmail.com"


def test_supabase_login_existing_user(client: TestClient):
    """An existing user logging in via Supabase Gmail gets authenticated into their existing account."""
    from app.services import auth_service

    with session_scope() as db:
        existing = auth_service.create_user(
            db, email="prior.user@gmail.com", password="SecurePassword123!", full_name="Prior User"
        )
        existing_id = existing.id

    payload = {
        "access_token": "mock-supabase-token-2",
        "email": "prior.user@gmail.com",
        "full_name": "Prior User Updated",
    }
    res = client.post("/api/auth/supabase-login", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["id"] == existing_id
    assert data["user"]["email"] == "prior.user@gmail.com"


def test_supabase_login_inactive_user_rejected(client: TestClient):
    """Deactivated accounts cannot authenticate via Supabase."""
    from app.services import auth_service

    with session_scope() as db:
        inactive_user = auth_service.create_user(
            db, email="disabled@gmail.com", password="SecurePassword123!", full_name="Disabled User"
        )
        inactive_user.is_active = False
        db.commit()

    payload = {
        "access_token": "mock-supabase-token-3",
        "email": "disabled@gmail.com",
    }
    res = client.post("/api/auth/supabase-login", json=payload)
    assert res.status_code == 401
    assert "inactive" in res.text.lower()


def test_supabase_jwt_secret_validation(client: TestClient, monkeypatch):
    """When SUPABASE_JWT_SECRET is set, valid JWTs signed with that secret are accepted."""
    test_secret = "super-secret-jwt-signing-key-123456"
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", test_secret)

    claims = {
        "sub": "supabase-user-uuid-999",
        "email": "jwt.verified@gmail.com",
        "user_metadata": {"full_name": "JWT Verified Lead"},
        "aud": "authenticated",
    }
    signed_jwt = jwt.encode(claims, test_secret, algorithm="HS256")

    res = client.post("/api/auth/supabase-login", json={"access_token": signed_jwt})
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["email"] == "jwt.verified@gmail.com"
    assert data["user"]["full_name"] == "JWT Verified Lead"


def test_supabase_login_invalid_token(client: TestClient):
    """An unparseable token returns 401."""
    res = client.post("/api/auth/supabase-login", json={"access_token": "bad.unparseable.token"})
    assert res.status_code == 401
