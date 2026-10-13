# Troubleshooting Guide

Real problems encountered while building and verifying LocalLead, with the
actual fixes.

## 1. Two Celery workers competing for jobs (duplicate workers)

**Symptom:** a search job completes but businesses have no crawl results, or
jobs behave as if crawling were disabled. Health shows "workers ok" yet the
Docker worker log shows no task received.

**Root cause:** a host (non-Docker) Celery worker from a previous dev run is
still connected to the same Redis broker. Celery delivers each task to exactly
one worker, so the host worker can "steal" jobs and run them with a different
environment (e.g. `ENABLE_WEBSITE_CRAWLING=false`).

**Detect it:**

```bash
# host processes (must output NOTHING while the Docker stack is up)
ps aux | grep -E "celery|uvicorn" | grep -v grep

# which worker names are connected to the broker?
docker compose exec redis redis-cli pubsub numsub

# or check for the host-named worker in the worker log:
docker compose logs worker | grep -i "mingle"
# "sync with 1 nodes" instead of "all alone" = a second worker is connected
```

Also check for stale port listeners: `lsof -iTCP:8000 -sTCP:LISTEN` should
show only Docker's proxy while the stack runs.

**Fix:** kill the host processes (`pkill -f "celery.*app.workers"`,
`pkill -f "uvicorn app.main"`) and re-run. The bundled harness scripts
(`scripts/live_e2e.py`, `scripts/test_cancel.py`, `scripts/load_test.py`)
now bind uvicorn to a unique ephemeral port per run and terminate their own
process groups on exit.

## 2. 502 Bad Gateway after recreating a container

**Symptom:** `docker compose up -d --build api` succeeds, the API is healthy,
but nginx returns 502 until nginx itself is restarted.

**Root cause:** nginx resolves `upstream server api:8000` once at startup. A
recreated container gets a new IP and nginx keeps proxying to the old one.

**Fix (applied):** `infrastructure/nginx/nginx.conf` uses Docker's embedded
DNS resolver with variable upstreams, so resolution happens per request:

```nginx
resolver 127.0.0.11 valid=10s ipv6=off;
set $api_upstream api:8000;
```

Note that with variable upstreams nginx passes the URI verbatim, so the
`/backend` prefix is stripped with an explicit `rewrite` in
`locallead_proxy_params.conf`.

## 3. CSP blocking API calls after adding security headers

**Symptom:** the UI renders but every API call fails with
`Refused to connect … violates Content-Security-Policy directive: "connect-src"`.

**Root cause:** a CSP source with a *path* (e.g. `http://localhost:8080/backend`)
only matches that exact path, not `/backend/api/*`.

**Fix (applied):** `next.config.mjs` derives `connect-src` from the *origin*
of `NEXT_PUBLIC_API_URL`, never the full URL with a path.

## 4. Register/login returns 429 during test or load scripts

**Symptom:** `Too many registrations; try later` when running local harness
scripts repeatedly.

**Root cause:** per-IP registration limits (5/hour) working as designed —
the script is the "attacker" here.

**Fix:** the bundled scripts set `RATE_LIMIT_DISABLE_LOOPBACK=true` in their
spawned environment (opt-in, default off; production limits always apply).
For the Docker stack, register a dedicated test user instead of scripting
registrations against it.

## 5. nginx rejects config: "\"location\" directive is not allowed here"

**Root cause:** a shared params file mounted inside `/etc/nginx/conf.d/` is
auto-loaded at http level, then included again inside the server block.

**Fix (applied):** the params file is mounted at
`/etc/nginx/locallead_proxy_params.conf` (outside `conf.d/`).

## 6. DNS failures misclassified as SSRF blocks

**Symptom:** dead domains show `ssrf_blocked` in crawl results, making every
unresolvable mock/test domain look like a security event.

**Fix (applied):** `URLGuard.check` returns a `kind` field
(`ssrf_blocked` vs `dns_error`); the fetcher records the honest status. Only
genuinely blocked addresses (private ranges, metadata IPs) are `ssrf_blocked`.

## 7. Worker container reports unhealthy during long jobs

The worker healthcheck runs `celery inspect ping`. A `--pool=solo` worker
processing a long task may miss the ping; the healthcheck tolerates this
(`start_period`, retries). If it stays unhealthy, check the worker log for
stuck tasks and restart the service.
