"""Job progress reporting.

Dual-channel progress: durable status in Postgres (search_jobs row) and
ephemeral events via Redis pub/sub consumed by the SSE endpoint. In tests or
Redis-less environments the pub/sub publisher degrades to a no-op.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional, Any

from app.core.logging_config import event_logger
from app.models.database import session_scope
from app.models.models import SearchJob
from app.utils.datetime_utils import utcnow

log = event_logger("locallead.progress")

CHANNEL = "locallead:job_events"


def publish_job_event(event: dict[str, Any]) -> None:
    """Public wrapper so other components (e.g. the cancel endpoint) can push
    an SSE event immediately; best-effort, never raises."""
    _publish(event)


def _publish(event: dict[str, Any]) -> None:
    """Best-effort Redis pub/sub publish; never raises."""
    try:
        import redis

        from app.core.config import get_settings

        client = redis.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        client.publish(CHANNEL, json.dumps(event, default=str))
    except Exception:  # noqa: BLE001 — progress events are non-critical
        pass


def update_job(db, job_id: str, *, status: Optional[str] = None,
               stage_detail: str = "", progress_delta: int = 0,
               progress_set: Optional[int] = None, **counters) -> None:
    """Persist job state changes and publish an SSE event.

    ``counters`` accepts: total, completed, failed, emails_found,
    phones_found, websites_found.
    """
    job = db.get(SearchJob, job_id)
    if job is None:
        log.error("update_job_missing", job_id=job_id)
        return
    if job.status == "cancelled" and status not in (None, "cancelled"):
        # honour cancellation: stop advancing a cancelled job
        return
    if status:
        job.status = status
    if stage_detail:
        job.stage_detail = stage_detail[:250]
    if progress_set is not None:
        job.progress = max(0, min(100, progress_set))
    else:
        job.progress = max(0, min(100, job.progress + progress_delta))
    for key, value in counters.items():
        if hasattr(job, key):
            setattr(job, key, value)
    if status == "completed":
        job.finished_at = utcnow()
        job.progress = 100
    elif status == "failed":
        job.finished_at = utcnow()
    elif status in ("discovering", "enriching", "crawling", "extracting",
                    "deduplicating", "generating_report") and not job.started_at:
        job.started_at = utcnow()
    db.commit()

    _publish({
        "job_id": job_id,
        "status": job.status,
        "stage_detail": job.stage_detail,
        "progress": job.progress,
        "total": job.total,
        "completed": job.completed,
        "failed": job.failed,
        "emails_found": job.emails_found,
        "phones_found": job.phones_found,
        "websites_found": job.websites_found,
        "ts": utcnow().isoformat(),
    })


def job_event_payload(job_id: str, event: str, **fields) -> dict[str, Any]:
    """Build a job-scoped event payload with an id for SSE `id:` lines."""
    return {"id": uuid.uuid4().hex, "event": event, "job_id": job_id, **fields}
