"""Celery application for background search jobs."""
from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import get_settings
from app.core.logging_config import setup_logging


def make_celery() -> Celery:
    """Build the Celery app from settings."""
    settings = get_settings()
    app = Celery(
        "locallead",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=["app.workers.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=settings.celery_task_soft_time_limit,
        task_time_limit=settings.celery_task_time_limit,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = make_celery()


@worker_process_init.connect
def _reset_db_engine_after_fork(**_kwargs) -> None:
    """Each worker process must build its own SQLAlchemy engine."""
    from app.models.database import reset_engine

    reset_engine()
    setup_logging()
