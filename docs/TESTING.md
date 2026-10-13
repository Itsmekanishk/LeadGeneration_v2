# Testing

## Suites

| Suite | Location | What it proves |
|---|---|---|
| Unit/API (pytest) | `backend/tests/` | parser (all example queries), dedup, normalization, crawler vs local HTTP fixtures, provider behavior, API auth/RBAC/rate limits, exports (Unicode, formula injection, 500-row scale), SSRF matrix |
| SSRF | `backend/tests/test_ssrf.py` | blocked IP ranges (IPv4+IPv6), hostnames, schemes, credentials, DNS-resolution blocking, redirect re-validation, honest `dns_error` vs `ssrf_blocked` classification, allowlist isolation |
| Live E2E | `backend/scripts/live_e2e.py` | real uvicorn + real Celery + real Postgres/Redis: register → search → SSE events → results/filters/summary → PDF/CSV/XLSX generate+download → ownership/RBAC → artifacts on disk (39 checks) |
| Cancellation | `backend/scripts/test_cancel.py` | cancel during run → terminal `cancelled` state, no stuck rows; cancel after completion is a no-op; SSE announces cancellation (9 checks) |
| Load | `backend/scripts/load_test.py` | sequential + concurrent mock jobs through the real worker; throughput, queue behavior, DB session health |
| Browser E2E | manual via preview (nginx :8080) | register/login → interpretation → live SSE progress → results → filters → detail → exports → history → logout → auth-guard bounce; zero console errors |
| Frontend build | `cd frontend && npm run build` | Next.js production build + TypeScript |

## Running

```bash
cd backend
./.venv/bin/python -m pytest -q                       # unit/API/SSRF/exports

DATABASE_URL=postgresql+psycopg2://locallead:locallead@localhost:5432/locallead \
REDIS_URL=redis://localhost:6379/0 \
./.venv/bin/python scripts/live_e2e.py                # spawns its own api+worker

./.venv/bin/python scripts/test_cancel.py
./.venv/bin/python scripts/load_test.py

cd ../frontend && npm run build
```

The harness scripts spawn and terminate their own uvicorn + Celery processes
on an ephemeral port; do not run them against a stack you care about without
reading `docs/TROUBLESHOOTING.md` §1 (duplicate workers).

## Conventions

- Tests never call third-party websites: crawler fixtures are local HTTP
  servers; business data is the mock provider.
- `SSRF_ALLOWED_HOSTS=127.0.0.1` is used so the local-HTTP crawler fixtures
  run **with SSRF protection on**.
- No test is skipped or weakened to make a suite pass; failing tests are
  fixed at the root cause.
- External dependencies (live Google API, SMTP delivery, TLS certificates)
  are excluded from CI by design and reported as UNVERIFIED until real
  credentials exist.
