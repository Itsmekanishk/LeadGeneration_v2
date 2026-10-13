"""Health endpoint: DB, Redis, Celery workers, provider readiness."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.models.database import get_engine
from app.providers.factory import get_provider
from app.schemas.common import HealthOut

router = APIRouter(tags=["health"])


def _check_db() -> str:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


def _check_redis() -> str:
    try:
        import redis

        client = redis.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        return "ok" if client.ping() else "error"
    except Exception:  # noqa: BLE001
        return "error"


def _check_workers() -> str:
    try:
        from app.workers.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=2)
        stats = inspect.ping()
        return "ok" if stats else "no_workers"
    except Exception:  # noqa: BLE001
        return "unknown"


def _check_provider() -> str:
    try:
        provider = get_provider()
        return "ok" if provider.health_check() else "misconfigured"
    except Exception as exc:  # noqa: BLE001
        return f"error: {str(exc)[:80]}"


@router.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness + dependency readiness report."""
    settings = get_settings()
    return HealthOut(
        status="ok",
        app_env=settings.app_env,
        checks={
            "database": _check_db(),
            "redis": _check_redis(),
            "workers": _check_workers(),
            "provider": _check_provider(),
        },
    )
