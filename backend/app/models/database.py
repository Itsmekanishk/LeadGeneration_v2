"""SQLAlchemy declarative base and session management.

The FastAPI app uses dependency injection (``get_db``); Celery workers build
their own short-lived sessions via ``session_scope`` context manager.
"""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models.base import Base

_engine = None
_SessionLocal = None


def get_engine():
    """Lazily-created engine; safe under fork (Celery recreates after fork)."""
    global _engine, _SessionLocal  # noqa: PLW0603
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(
                pool_size=10,
                max_overflow=20,
                pool_recycle=1800,
            )
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker:
    """Return (and lazily build) the session factory."""
    get_engine()
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a DB session."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context-managed session for worker code; commits on success."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_engine() -> None:
    """Dispose engine (used by tests and Celery prefork re-init)."""
    global _engine, _SessionLocal  # noqa: PLW0603
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
