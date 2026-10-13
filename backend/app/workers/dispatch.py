"""Job dispatch indirection.

Production dispatches the Celery task; tests can monkeypatch
``app.workers.dispatch.dispatch_search_job`` to run the pipeline inline.

A search must never silently sit in ``queued`` forever. Celery is only used
when Redis is reachable *and* at least one worker has registered with the
broker; otherwise (broker down, or the worker crashed/never started) the job
runs in a local background thread so it still completes.
"""
from __future__ import annotations


import logging
import threading
import time

log = logging.getLogger("locallead.dispatch")

# Cache the worker-liveness probe: a Celery ``inspect().ping()`` costs up to
# the timeout on every miss, and we only want to pay that when a worker might
# have just come up or died.
_WORKER_CACHE_TTL_SECONDS = 15.0
_worker_cache: dict[str, float | bool] = {"checked_at": 0.0, "alive": False}


def _is_redis_available() -> bool:
    try:
        import redis
        from app.core.config import get_settings

        client = redis.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=0.2, socket_timeout=0.2
        )
        return bool(client.ping())
    except Exception:
        return False


def _has_live_workers(now: float | None = None) -> bool:
    """True when at least one Celery worker answers a broker ping.

    Result is cached for a few seconds so a broker-up/worker-down state does
    not add a probe timeout to every single search request.
    """
    now = time.monotonic() if now is None else now
    if now - float(_worker_cache["checked_at"]) < _WORKER_CACHE_TTL_SECONDS:
        return bool(_worker_cache["alive"])
    alive = False
    try:
        from app.workers.celery_app import celery_app

        stats = celery_app.control.inspect(timeout=1.0).ping()
        alive = bool(stats)
    except Exception as exc:  # noqa: BLE001 — treat any probe failure as "no workers"
        log.debug("worker_liveness_probe_failed: %s", exc)
        alive = False
    _worker_cache["checked_at"] = now
    _worker_cache["alive"] = alive
    return alive


def _run_inline(job_id: str) -> None:
    try:
        from app.workers.tasks import run_search_job

        log.info("Running job %s in local background thread", job_id)
        run_search_job(job_id)
    except Exception as exc:
        log.exception("Local thread worker failed for job %s: %s", job_id, exc)


def dispatch_search_job(job_id: str) -> None:
    """Send the search job to the Celery worker, with automatic fallback for local dev.

    Uses Celery only when the broker is reachable *and* a worker is alive;
    otherwise the job runs in a local thread. This is what makes the stack
    work in mock/SQLite dev and keeps a crashed worker from stalling searches.
    """
    if _is_redis_available():
        if _has_live_workers():
            try:
                from app.workers.celery_app import celery_app

                celery_app.send_task("locallead.run_search_job", args=[job_id])
                return
            except Exception as exc:
                log.warning(
                    "Celery dispatch failed (%s); falling back to background thread", exc
                )
        else:
            log.info(
                "No Celery worker alive; dispatching job %s to local thread worker",
                job_id,
            )
    else:
        log.info(
            "Redis broker unavailable; dispatching job %s to local thread worker", job_id
        )

    thread = threading.Thread(target=_run_inline, args=(job_id,), daemon=True)
    thread.start()
