"""Mock-mode scale/load test (Phase 30) using the live_e2e harness.

Spawns a real uvicorn + Celery worker against real Postgres/Redis, then runs
jobs at the API schema cap (max_results=200 — the le=200 constraint) plus
concurrent jobs, measuring wall time and throughput.

Honest scale note: with the mock provider each search request yields at most
12 businesses (MOCK_RESULTS cap), so a "200" job contains 12 rows — the point
here is end-to-end pipeline/DB/queue behavior, not row-count scaling. Real
row scaling is covered by export tests (500-row PDF/XLSX) and by a live
Google pilot once credentials exist.
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

CRAWLING = os.environ.get("LOAD_CRAWLING", "false") == "true"


def main() -> int:
    venv = h.BASE / ".venv" / "bin"
    Path("/tmp/locallead").mkdir(parents=True, exist_ok=True)
    log_api = open("/tmp/locallead/load_api.log", "w")
    log_worker = open("/tmp/locallead/load_worker.log", "w")
    procs: list = []

    def spawn(args, log):
        p = subprocess.Popen(args, env=h.ENV, cwd=str(h.BASE), stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        procs.append(p)
        return p

    api = spawn([str(venv / "uvicorn"), "app.main:app", "--host", "127.0.0.1",
                 "--port", "8000", "--workers", "2"], log_api)
    if not h.wait_health(api, "api", 40):
        print(open("/tmp/locallead/load_api.log").read()[-2000:])
        return h.teardown(procs, 1)
    worker = spawn([str(venv / "celery"), "-A", "app.workers.celery_app.celery_app",
                    "worker", "--loglevel=WARNING", "--concurrency=2", "--pool=solo"],
                   log_worker)
    deadline = time.time() + 40
    while time.time() < deadline and worker.poll() is None:
        if "ready." in open("/tmp/locallead/load_worker.log").read():
            break
        time.sleep(1)

    run = str(int(time.time()))[-6:]
    email = f"load-{run}-{os.getpid()}@verify-example.com"
    tok = None
    for attempt in range(6):
        status, body, _ = h.req("POST", "/api/auth/register", body={
            "email": email, "password": "password123", "full_name": "Load Test"})
        if "access_token" in body:
            tok = body["access_token"]
            break
        print(f"  register attempt {attempt + 1}: HTTP {status} {body} — retrying in 20s")
        time.sleep(20)  # auth rate-limit window
    if not tok:
        print("FAIL could not register load-test user")
        return h.teardown(procs, 1)

    def create(size):
        status, body, _ = h.req("POST", "/api/search", token=tok, body={
            "free_text": "Hospitals in Porwal Road Pune",
            "max_results": size, "provider": "mock"})
        return body.get("job_id"), status

    def wait(job_id, timeout_s=300):
        """Poll until terminal; row count lives in progress_stats.total."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            _, j, _ = h.req("GET", f"/api/search/{job_id}", token=tok)
            if j.get("status") in ("completed", "failed", "cancelled"):
                total = j.get("progress_stats", {}).get("total", 0)
                return j["status"], time.time() - t0, total
            time.sleep(0.4)
        return "timeout", time.time() - t0, 0

    failures = 0
    print(f"mode: crawling={'on' if CRAWLING else 'off'} (mock provider)\n")

    # --- sequential jobs at schema cap (200) --------------------------------
    for i in range(3):
        t0 = time.time()
        job_id, status = create(200)
        if status not in (200, 202):
            h.check(f"create job #{i+1} (max_results=200)", False, f"HTTP {status}")
            failures += 1
            continue
        final, dur, total = wait(job_id)
        # Mock provider yields 12 businesses per search request (provider cap);
        # API schema caps requested max_results at 200.
        ok = final == "completed" and total == 12
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL'} job #{i+1}: status={final} total={total} "
              f"dur={dur:4.1f}s")

    # --- 4 concurrent jobs ----------------------------------------------------
    t0 = time.time()
    jobs = [create(200) for _ in range(4)]
    ok_create = all(s in (200, 202) for _, s in jobs)
    results = [wait(j) for j, _ in jobs]
    wall = time.time() - t0
    ok = ok_create and all(f == "completed" for f, _, _ in results)
    if not ok:
        failures += 1
    print(f"{'PASS' if ok else 'FAIL'} 4 concurrent jobs: "
          f"{[f for f, _, _ in results]} wall={wall:.1f}s "
          f"totals={[t for _, _, t in results]}")

    # --- DB health after load --------------------------------------------------
    check_db = subprocess.run(
        ["docker", "compose", "-f", str(h.BACKEND.parent / "docker-compose.yml"),
         "exec", "-T", "postgres", "psql", "-U", "locallead", "-d", "locallead",
         "-tAc", "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='locallead';"],
        capture_output=True, text=True)
    print(f"info: postgres sessions={check_db.stdout.strip() or 'n/a'}")

    print(f"\n=== load test {'PASSED' if failures == 0 else f'{failures} FAILURES'} ===")
    return h.teardown(procs, 0 if failures == 0 else 1)


if __name__ == "__main__":
    sys.exit(main())
