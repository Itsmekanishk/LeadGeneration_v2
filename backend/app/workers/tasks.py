"""Celery tasks: job entrypoints."""
from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from app.core.logging_config import event_logger
from app.models.database import session_scope
from app.models.models import SearchJob
from app.workers.pipeline import run_pipeline
from app.workers.progress import _publish, update_job
from app.utils.datetime_utils import utcnow

log = event_logger("locallead.tasks")


def is_job_cancelled(job_id: str) -> bool:
    """True when the job row is marked cancelled."""
    with session_scope() as db:
        job = db.get(SearchJob, job_id)
        return bool(job and job.status == "cancelled")


def run_search_job(job_id: str) -> dict:
    """Execute the search pipeline for *job_id* inside one worker task."""
    log.info("search_job_started", job_id=job_id)
    try:
        with session_scope() as db:
            job = db.get(SearchJob, job_id)
            if job is None:
                log.error("search_job_missing", job_id=job_id)
                return {"job_id": job_id, "status": "missing"}
            if job.status == "cancelled":
                log.info("search_job_cancelled_before_start", job_id=job_id)
                return {"job_id": job_id, "status": "cancelled"}
            job.started_at = job.started_at or utcnow()
            db.commit()

        with session_scope() as db:
            run_pipeline(db, job_id)

        with session_scope() as db:
            job = db.get(SearchJob, job_id)
            final_status = job.status if job else "unknown"
        _publish({"job_id": job_id, "event": "job_finished", "status": final_status})
        log.info("search_job_finished", job_id=job_id, status=final_status)
        return {"job_id": job_id, "status": final_status}
    except SoftTimeLimitExceeded:
        with session_scope() as db:
            update_job(db, job_id, status="failed",
                       stage_detail="Job timed out",
                       error_message="Exceeded maximum allowed duration")
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("search_job_failed", job_id=job_id, error=str(exc)[:500])
        raise


# Celery registration ---------------------------------------------------------

from app.workers.celery_app import celery_app  # noqa: E402

celery_app.task(name="locallead.run_search_job")(run_search_job)
