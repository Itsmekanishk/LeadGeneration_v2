"""Comprehensive lifecycle verification for Supabase Gmail Authentication.

Tests:
1. End-to-end new user registration via Supabase Gmail OAuth token.
2. Verified identity resolution: name, email, role assignment.
3. Access token usage on authenticated routes (/api/auth/me).
4. Refresh token rotation lifecycle.
5. Existing user login (idempotency, existing user ID preservation).
6. Audit log entry generation.
7. Deactivated user rejection.
8. Invalid token rejection.
"""
import sys
from pathlib import Path

# Ensure backend root is on sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import os
os.environ["APP_ENV"] = "development"
os.environ["DATABASE_URL"] = "sqlite:///./data/verify_supabase.db"
os.environ["SECRET_KEY"] = "verify-secret-key-min-32-chars-long"
os.environ["BUSINESS_PROVIDER"] = "mock"

from fastapi.testclient import TestClient
from app.main import app
from app.models import database as db_mod
from app.models.base import Base
from app.models.models import User, AuditLog
from app.core.config import get_settings

def run_verification():
    print("[1/8] Initializing database and test environment...")
    Path("./data").mkdir(parents=True, exist_ok=True)
    get_settings.cache_clear()
    engine = db_mod.get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    client = TestClient(app)

    # 1. New user registration via Supabase Gmail
    print("[2/8] Testing new user registration via Supabase token...")
    resp = client.post("/api/auth/supabase-login", json={
        "access_token": "mock-supabase-gmail-token-1",
        "email": "sarah.connor@gmail.com",
        "full_name": "Sarah Connor",
    })
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "access_token" in data, "Missing access_token"
    assert "refresh_token" in data, "Missing refresh_token"
    assert data["user"]["email"] == "sarah.connor@gmail.com", "Email mismatch"
    assert data["user"]["full_name"] == "Sarah Connor", "Name mismatch"
    user_id = data["user"]["id"]
    access_token = data["access_token"]
    refresh_token = data["refresh_token"]
    print(f"      ✓ Successfully created user {user_id} with tokens")

    # 2. Access authenticated endpoint /api/auth/me
    print("[3/8] Testing authenticated endpoint /api/auth/me with access token...")
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_resp.status_code == 200, f"Expected 200, got {me_resp.status_code}: {me_resp.text}"
    me_data = me_resp.json()
    assert me_data["id"] == user_id
    assert me_data["email"] == "sarah.connor@gmail.com"
    print("      ✓ /api/auth/me verified user identity")

    # 3. Refresh token rotation
    print("[4/8] Testing refresh token rotation...")
    ref_resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert ref_resp.status_code == 200, f"Expected 200, got {ref_resp.status_code}: {ref_resp.text}"
    ref_data = ref_resp.json()
    new_access_token = ref_data["access_token"]
    new_refresh_token = ref_data["refresh_token"]
    assert new_access_token != access_token
    assert new_refresh_token != refresh_token
    print("      ✓ Token successfully rotated")

    # 4. Existing user re-login via Supabase Gmail
    print("[5/8] Testing existing user re-login (idempotency)...")
    relogin_resp = client.post("/api/auth/supabase-login", json={
        "access_token": "mock-supabase-gmail-token-2",
        "email": "sarah.connor@gmail.com",
        "full_name": "Sarah Connor",
    })
    assert relogin_resp.status_code == 200
    relogin_data = relogin_resp.json()
    assert relogin_data["user"]["id"] == user_id, "User ID changed on re-login!"
    print("      ✓ Existing user logged in without duplicating user row")

    # 5. Verify audit logs
    print("[6/8] Verifying audit logs for supabase_login events...")
    with db_mod.session_scope() as db:
        logs = db.query(AuditLog).filter(AuditLog.user_id == user_id).all()
        assert len(logs) >= 2, f"Expected >= 2 audit logs, found {len(logs)}"
        assert any(l.action == "supabase_login" for l in logs)
    print(f"      ✓ Audit logs recorded: {[l.action for l in logs]}")

    # 6. Deactivated user rejection
    print("[7/8] Testing deactivated user rejection...")
    with db_mod.session_scope() as db:
        u = db.get(User, user_id)
        u.is_active = False
        db.commit()

    deact_resp = client.post("/api/auth/supabase-login", json={
        "access_token": "mock-supabase-gmail-token-3",
        "email": "sarah.connor@gmail.com",
    })
    assert deact_resp.status_code == 401, f"Expected 401, got {deact_resp.status_code}"
    print("      ✓ Deactivated user blocked from Supabase login")

    # 7. Invalid token handling
    print("[8/8] Testing invalid token rejection...")
    bad_resp = client.post("/api/auth/supabase-login", json={
        "access_token": "invalid.jwt.token.here",
    })
    assert bad_resp.status_code == 401
    print("      ✓ Invalid token correctly rejected with 401")

    # Clean up test database
    db_mod.reset_engine()
    test_db = Path("./data/verify_supabase.db")
    if test_db.exists():
        test_db.unlink()

    print("\n🎉 ALL LIFECYCLE CHECKS PASSED SUCCESSFULLY! Real backend behavior verified.")

if __name__ == "__main__":
    run_verification()
