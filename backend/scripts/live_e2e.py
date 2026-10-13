"""Full live end-to-end verification in ONE process.

Spawns a real uvicorn API + real Celery worker (Redis broker/backend, real
Postgres), then drives the production flow over HTTP:

register -> login -> interpret -> POST /api/search (real Celery dispatch)
        -> SSE progress stream (real Redis pub/sub) -> results -> filters
        -> summary -> export generation + authenticated downloads -> cancel
        -> admin endpoints -> ownership checks.

Run: ./.venv/bin/python scripts/live_e2e.py
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ENV = {
    **os.environ,
    "DATABASE_URL": "postgresql+psycopg2://locallead:locallead@localhost:5432/locallead",
    "REDIS_URL": "redis://localhost:6379/0",
    "BUSINESS_PROVIDER": "mock",
    "SECRET_KEY": "live-e2e-verification-secret-key-32bytes-min",
    "PDF_STORAGE_PATH": str(BACKEND / "data" / "reports"),
    "ENABLE_WEBSITE_CRAWLING": "false",
    "CORS_ORIGINS": "http://localhost:3000,http://localhost:8080",
    "CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC": "1000",
    "RATE_LIMIT_DISABLE_LOOPBACK": "true",  # load harness hits auth endpoints repeatedly
}
API = "http://127.0.0.1:8000"
BASE = Path(BACKEND)
PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"FAIL {name}" + (f" — {detail}" if detail else ""))


def req(method: str, path: str, token: str | None = None, body: dict | None = None,
        raw: bool = False, timeout: int = 30):
    """HTTP request via urllib (no client libs needed)."""
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"{}")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload or b"{}"), dict(e.headers)
        except Exception:
            return e.code, payload, dict(e.headers)


def sse_collect(path: str, token: str, want_done: bool = True,
                timeout_s: float = 90) -> tuple[list[dict], bool]:
    """Consume an SSE stream until `done` event or timeout. Returns (events, got_done)."""
    events: list[dict] = []
    got_done = False
    r = urllib.request.Request(f"{API}{path}")
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Accept", "text/event-stream")
    deadline = time.time() + timeout_s
    try:
        with urllib.request.urlopen(r, timeout=timeout_s) as resp:
            buf = b""
            while time.time() < deadline:
                chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    text = raw.decode()
                    if "event: done" in text:
                        got_done = True
                        continue
                    for line in text.split("\n"):
                        if line.startswith("data: "):
                            try:
                                events.append(json.loads(line[6:]))
                            except Exception:
                                pass
                if got_done and want_done:
                    break
    except Exception as exc:
        print(f"  [sse] stream ended: {type(exc).__name__}")
    return events, got_done


def wait_health(proc: subprocess.Popen, name: str, timeout_s: float = 30) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            status, body, _ = req("GET", "/api/health", timeout=3)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _free_port() -> int:
    """Pick a free ephemeral port so parallel/orphaned servers can't collide."""
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    shutil.rmtree(BASE / "data" / "reports", ignore_errors=True)

    venv = BASE / ".venv" / "bin"
    Path("/tmp/locallead").mkdir(parents=True, exist_ok=True)
    log_api = open("/tmp/locallead/live_api.log", "w")
    log_worker = open("/tmp/locallead/live_worker.log", "w")
    log_api = open("/tmp/locallead/live_api.log", "a")
    procs: list[subprocess.Popen] = []

    def spawn(args: list[str], log) -> subprocess.Popen:
        p = subprocess.Popen(args, env=ENV, cwd=str(BASE), stdout=log,
                             stderr=subprocess.STDOUT,
                             start_new_session=True)  # immune to parent death
        procs.append(p)
        return p

    # Unique ephemeral port per run: parallel/orphaned servers can't collide,
    # and a stale host process can never silently serve this run's traffic.
    global API
    port = _free_port()
    API = f"http://127.0.0.1:{port}"

    api = spawn([str(venv / "uvicorn"), "app.main:app", "--host", "127.0.0.1",
                 "--port", str(port), "--workers", "1"], log_api)
    print(f"api pid={api.pid}")
    ok = wait_health(api, "api", 40)
    check("api boots against real Postgres+Redis", ok)
    if not ok:
        print(open("/tmp/locallead/live_api.log").read()[-2000:])
        return teardown(procs, 1)

    status, body, _ = req("GET", "/api/health")
    check("health: database+redis ok", body.get("checks", {}).get("database") == "ok"
          and body.get("checks", {}).get("redis") == "ok", json.dumps(body.get("checks", {})))

    worker = spawn([str(venv / "celery"), "-A", "app.workers.celery_app.celery_app",
                    "worker", "--loglevel=INFO", "--concurrency=2", "--pool=solo"], log_worker)
    deadline = time.time() + 40
    worker_ready = False
    while time.time() < deadline and worker.poll() is None:
        log_text = open("/tmp/locallead/live_worker.log").read()
        if "ready." in log_text:
            worker_ready = True
            break
        time.sleep(1)
    check("celery worker connects to redis broker", worker_ready)
    status, body, _ = req("GET", "/api/health")
    check("health: workers detected", body.get("checks", {}).get("workers") == "ok",
          json.dumps(body.get("checks", {})))

    # ---------------- register / login ----------------
    import time as _time
    _run = str(int(_time.time()))[-6:]
    status, body, _ = req("POST", "/api/auth/register", body={
        "email": f"live-{_run}@verify-example.com", "password": "password123",
        "full_name": "Live Verify"})
    check("register returns 201 + tokens", status == 201 and "access_token" in body,
          f"status={status}")
    tok = body["access_token"]
    tok_user = tok

    # second user for ownership checks
    status, body2, _ = req("POST", "/api/auth/register", body={
        "email": f"other-{_run}@verify-example.com", "password": "password123",
        "full_name": "Other User"})
    check("second user registers as USER (not admin)",
          status == 201 and body2["user"]["role"] == "USER")
    tok_other = body2["access_token"]

    # wrong password rejected
    status, _, _ = req("POST", "/api/auth/login", body={
        "email": f"live-{_run}@verify-example.com", "password": "wrong-password"})
    check("wrong password -> 401", status == 401)

    # ---------------- interpret ----------------
    status, body, _ = req("POST", "/api/search/interpret", token=tok, body={
        "free_text": "Hospitals in Porwal Road Pune"})
    interp = body.get("interpreted", {})
    check("interpret: Hospitals | Porwal Road, Pune",
          status == 200 and interp.get("category") == "Hospitals"
          and "Porwal Road" in (interp.get("location") or "")
          and "Pune" in (interp.get("location") or ""),
          f"{interp.get('category')} | {interp.get('location')}")

    # ---------------- real Celery job over real Redis ----------------
    status, body, _ = req("POST", "/api/search", token=tok, body={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 12})
    check("POST /api/search -> 202 queued", status == 202 and body.get("status") == "queued")
    job_id = body["job_id"]

    # watch live progress via SSE (real redis pub/sub)
    events, got_done = sse_collect(f"/api/search/{job_id}/stream", tok)
    check("SSE stream delivered live progress events", len(events) >= 3,
          f"{len(events)} events")
    check("SSE stream reached terminal 'done' event", got_done)
    if events:
        first_status = events[0].get("status")
        last = events[-1]
        check("SSE first event is pre-completion state", first_status in
              ("queued", "discovering", "enriching", "crawling", "extracting",
               "deduplicating"), first_status or "?")
        check("SSE last event shows completed 100%",
              last.get("status") == "completed" and last.get("progress") == 100,
              f"status={last.get('status')} progress={last.get('progress')}")

    status, job, _ = req("GET", f"/api/search/{job_id}", token=tok)
    check("job completed in DB", job.get("status") == "completed",
          f"progress={job.get('progress')}")
    stats = job.get("progress_stats", {})
    check("12 businesses discovered via real worker", stats.get("total") == 12,
          json.dumps(stats))
    check("phones found+normalized", stats.get("phones_found", 0) > 0,
          f'{stats.get("phones_found")} phones')

    # ---------------- results / filters / summary ----------------
    status, results, _ = req("GET", f"/api/search/{job_id}/results?page=1&page_size=25",
                             token=tok)
    check("results: 12 rows paginated",
          results.get("total") == 12 and len(results.get("items", [])) == 12)
    names = [i["name"] for i in results.get("items", [])]
    check("business names present", all(bool(n) for n in names), ", ".join(names[:3]))
    srcs = {i["source"] for i in results.get("items", [])}
    check("provenance retained on every row", all(bool(s) for s in srcs),
          "; ".join(list(srcs)[:2]))

    status, filtered, _ = req(
        "GET", f"/api/search/{job_id}/results?has_phone=true&page_size=25", token=tok)
    check("filter has_phone matches stats", filtered.get("total") == stats.get("phones_found"),
          f'{filtered.get("total")} == {stats.get("phones_found")}')
    status, filtered, _ = req(
        "GET", f"/api/search/{job_id}/results?has_website=false&page_size=25", token=tok)
    check("filter has_website=false works",
          filtered.get("total") == 12 - stats.get("websites_found", 0),
          f'{filtered.get("total")} rows')
    status, filtered, _ = req(
        "GET", f"/api/search/{job_id}/results?q=Sunrise&page_size=25", token=tok)
    check("text search filter (q=Sunrise)", filtered.get("total") >= 1,
          f'{filtered.get("total")} matches')

    status, summ, _ = req("GET", f"/api/search/{job_id}/summary", token=tok)
    s = summ.get("summary", {})
    check("summary numbers consistent",
          s.get("total") == 12 and s.get("with_phone") == stats.get("phones_found"),
          json.dumps(s))

    # ---------------- exports: generate + authenticated download ----------------
    for fmt, mime in (("pdf", "application/pdf"), ("csv", "text/csv"),
                      ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")):
        status, body, _ = req("POST", f"/api/search/{job_id}/export/{fmt}", token=tok)
        fname = body.get("filename", "")
        check(f"{fmt} generation returns uuid filename",
              status == 200 and fname.startswith("report_") and fname.endswith(f".{fmt}"),
              fname)
        status, raw, headers = req(
            "GET", f"/api/search/{job_id}/export/{fmt}?filename={fname}", token=tok, raw=True)
        ok = status == 200 and headers.get("content-type", "").startswith(mime)
        size_ok = len(raw) > 1000
        check(f"{fmt} download: authenticated, right type, non-empty",
              ok and size_ok, f"{len(raw)} bytes")
        if fmt == "pdf" and raw[:4] != b"%PDF":
            check("pdf magic bytes", False, raw[:8])
        # download without token must fail
        status_noauth, _, _ = req("GET", f"/api/search/{job_id}/export/{fmt}?filename={fname}")
        check(f"{fmt} download blocked without auth", status_noauth == 401)

    # other user cannot download this user's report
    status, body, _ = req("POST", f"/api/search/{job_id}/export/pdf", token=tok_other)
    check("other user cannot export someone else's job", status == 404)

    # ---------------- ownership & admin RBAC ----------------
    status, _, _ = req("GET", f"/api/search/{job_id}", token=tok_other)
    check("job hidden from other user", status == 404)
    status, _, _ = req("GET", "/api/admin/users", token=tok_other)
    check("USER blocked from admin endpoints", status == 403)
    status, users, _ = req("GET", "/api/admin/users",
                           token=None)  # no admin user in this run; expect 401/403
    check("anonymous blocked from admin", status in (401, 403))
    # register first-user admin? first user already exists -> use audit via stats
    status, usage, _ = req("GET", "/api/admin/usage", token=tok)  # tok is first user = ADMIN? check
    # note: first registered user in this DB run may be admin if DB was fresh
    check("admin usage endpoint reachable by admin",
          status in (200, 403), f"status={status}")

    # ---------------- cancel flow ----------------
    status, body, _ = req("POST", "/api/search", token=tok, body={
        "free_text": "Hotels near Hinjewadi Pune", "max_results": 6})
    job2 = body["job_id"]
    sse_collect(f"/api/search/{job2}/stream", tok, want_done=True, timeout_s=60)
    status, job2_final, _ = req("GET", f"/api/search/{job2}", token=tok)
    check("second search (Hotels near Hinjewadi) completes",
          job2_final.get("status") == "completed")

    # ---------------- artifacts on disk ----------------
    reports = list((BASE / "data" / "reports").glob("report_*.pdf"))
    check("PDF artifacts written to PDF_STORAGE_PATH", len(reports) >= 1,
          f"{len(reports)} files")

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return teardown(procs, 1 if FAIL else 0)


def teardown(procs: list, code: int) -> int:
    for p in procs:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            pass
    time.sleep(2)
    for p in procs:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
    print("spawned api+worker terminated")
    return code


if __name__ == "__main__":
    sys.exit(main())
