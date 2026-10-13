"""Shared pytest fixtures for the LocalLead backend test suite."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test.db")
os.environ.setdefault("BUSINESS_PROVIDER", "mock")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-16")
os.environ.setdefault("PDF_STORAGE_PATH", "./data/test-reports")
os.environ.setdefault("CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC", "1000")
os.environ.setdefault("ENABLE_WEBSITE_CRAWLING", "false")
os.environ["APP_ENV"] = "development"
os.environ["APILAYER_API_KEY"] = ""
os.environ["NUMVERIFY_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI TestClient bound to a fresh temp SQLite database."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{data_dir}/test.db")
    monkeypatch.setenv("PDF_STORAGE_PATH", str(data_dir / "reports"))
    get_settings.cache_clear()

    from app.models import database as db_mod
    from app.models.base import Base
    from app.main import app

    engine = db_mod.get_engine()
    Base.metadata.create_all(engine)

    # fresh rate-limit buckets per test (in-memory fallback)
    from app.core import rate_limiter

    rate_limiter._memory_buckets.clear()
    rate_limiter._redis_checked = True  # force memory limiter in tests
    rate_limiter._redis_client = None

    with TestClient(app) as c:
        yield c

    db_mod.reset_engine()
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture()
def user_tokens(client):
    """Create a regular USER directly in the DB, then log in via the API."""
    from app.core.security import hash_password
    from app.models.database import session_scope
    from app.models.models import User

    with session_scope() as db:
        db.add(User(
            email="user@user-test-example.com",
            hashed_password=hash_password("password123"),
            full_name="Test User", role="USER",
        ))
    resp = client.post("/api/auth/login", json={
        "email": "user@user-test-example.com", "password": "password123",
    })
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, \
        "user@user-test-example.com", "password123"


@pytest.fixture()
def admin_tokens(client):
    """Create an admin directly in the DB, then log in via the API."""
    from app.core.security import hash_password
    from app.models.database import session_scope
    from app.models.models import User

    with session_scope() as db:
        db.add(User(
            email="admin@user-test-example.com",
            hashed_password=hash_password("password123"),
            full_name="Admin", role="ADMIN",
        ))
    resp = client.post("/api/auth/login", json={
        "email": "admin@user-test-example.com", "password": "password123",
    })
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert tokens["user"]["role"] == "ADMIN"
    return {"Authorization": f"Bearer {tokens['access_token']}"}, \
        "admin@user-test-example.com", "password123"



