"""Live test of job cancellation semantics (Phase 13).

Spawns a real uvicorn + real Celery worker (same harness pattern as
live_e2e.py), then verifies:
1. cancel during a running job -> terminal state; a cancelled job must show
   status=cancelled and "Cancelled by user" (never stuck non-terminal)
2. cancel after completion -> no-op, stays completed
3. SSE stream of a cancelled job announces `cancelled` and closes with
   `event: done`
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import scripts.live_e2e as h  # noqa: E402


def create_job(tok: str, body: dict):
    """POST /api/search; API returns 202 Accepted (async) — accept 200 too."""
    status, body, _ = h.req("POST", "/api/search", token=tok, body=body)
    return status, body


def wait_status(token: str, job_id: str, wanted: set[str], timeout_s: float = 40):
    deadline = time.time() + timeout_s
    status = None
    while time.time() < deadline:
        _, body, _ = h.req("GET", f"/api/search/{job_id}", token=token)
        status = body.get("status")
        if status in wanted:
            return status
        time.sleep(0.3)
    return status


def main() -> int:
    venv = h.BASE / ".venv" / "bin"
    Path("/tmp/locallead").mkdir(parents=True, exist_ok=True)
    log_api = open("/tmp/locallead/cancel_api.log", "w")
    log_worker = open("/tmp/locallead/cancel_worker.log", "w")
    procs: list[subprocess.Popen] = []

    def spawn(args: list[str], log) -> subprocess.Popen:
        p = subprocess.Popen(args, env=h.ENV, cwd=str(h.BASE), stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        procs.append(p)
        return p

    api = spawn([str(venv / "uvicorn"), "app.main:app", "--host", "127.0.0.1",
                 "--port", "8000", "--workers", "1"], log_api)
    if not h.wait_health(api, "api", 40):
        print(open("/tmp/locallead/cancel_api.log").read()[-2000:])
        return h.teardown(procs, 1)
    worker = spawn([str(venv / "celery"), "-A", "app.workers.celery_app.celery_app",
                    "worker", "--loglevel=INFO", "--concurrency=1", "--pool=solo"],
                   log_worker)
    deadline = time.time() + 40
    while time.time() < deadline and worker.poll() is None:
        if "ready." in open("/tmp/locallead/cancel_worker.log").read():
            break
        time.sleep(1)

    run = str(int(time.time()))[-6:]
    status, body, _ = h.req("POST", "/api/auth/register", body={
        "email": f"cancel-{run}@verify-example.com", "password": "password123",
        "full_name": "Cancel Verify"})
    h.check("register returns tokens", status == 201 and "access_token" in body,
            f"status={status}")
    tok = body["access_token"]

    # --- 1. cancel during a running job ------------------------------------
    # Mock jobs finish in ~2s, so a late cancel loses the race. Retry until a
    # cancel actually lands (bounded attempts); either terminal outcome is
    # valid, but a stuck non-terminal state is NOT.
    job_id = None
    final = None
    for attempt in range(5):
        status, body = create_job(tok, {
            "free_text": "Hospitals in Porwal Road Pune", "max_results": 12})
        if status not in (200, 202) or not body.get("job_id"):
            h.check("job created", False, f"status={status}")
            return h.teardown(procs, 1)
        job_id = body["job_id"]
        h.req("POST", f"/api/search/{job_id}/cancel", token=tok)  # cancel ASAP
        final = wait_status(tok, job_id, {"completed", "failed", "cancelled"}, 40)
        if final == "cancelled":
            break
    h.check("job created", bool(job_id))
    h.check("cancel endpoint accepted (job terminal)",
            final in ("completed", "failed", "cancelled"), str(final))
    if final == "cancelled":
        _, j, _ = h.req("GET", f"/api/search/{job_id}", token=tok)
        h.check("cancelled job stage says cancelled",
                "cancel" in (j.get("stage_detail") or "").lower(), j.get("stage_detail"))
    else:
        print("  [info] worker always finished before cancel (acceptable race)")

    # --- 2. cancel after completion is a no-op ------------------------------
    status, body = create_job(tok, {
        "free_text": "Hotels near Hinjewadi Pune", "max_results": 6})
    h.check("second job created", status in (200, 202), f"status={status}")
    job2 = body["job_id"]
    final2 = wait_status(tok, job2, {"completed", "failed"}, 60)
    h.check("second job completed", final2 == "completed", str(final2))
    h.req("POST", f"/api/search/{job2}/cancel", token=tok)
    _, j2, _ = h.req("GET", f"/api/search/{job2}", token=tok)
    h.check("cancel after completion is a no-op", j2["status"] == "completed",
            j2["status"])

    # --- 3. SSE carries the cancelled state ---------------------------------
    # Retry create+cancel until one job actually lands cancelled, then verify
    # the deterministic stream contract: a stream opened on a cancelled job
    # must immediately emit its current state (cancelled) and close with done.
    got_cancelled = False
    got_done = False
    for attempt in range(5):
        status, body = create_job(tok, {
            "free_text": "Schools in Viman Nagar Pune", "max_results": 12})
        if status not in (200, 202):
            h.check("third job created", False, f"status={status}")
            return h.teardown(procs, 1)
        job3 = body["job_id"]
        h.req("POST", f"/api/search/{job3}/cancel", token=tok)
        final3 = wait_status(tok, job3, {"completed", "failed", "cancelled"}, 40)
        if final3 != "cancelled":
            continue
        events, got_done = h.sse_collect(f"/api/search/{job3}/stream", tok,
                                         want_done=True, timeout_s=30)
        got_cancelled = any(e.get("status") == "cancelled" for e in events)
        break
    h.check("SSE stream announced cancelled", got_cancelled)
    h.check("SSE stream closed with done event", got_done)

    print(f"\n=== {h.PASS} passed, {h.FAIL} failed ===")
    return h.teardown(procs, 1 if h.FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
