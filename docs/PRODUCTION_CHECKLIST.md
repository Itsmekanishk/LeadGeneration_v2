# Production Go-Live Checklist

## Configuration

- [ ] `APP_ENV=production`
- [ ] `SECRET_KEY` set to `openssl rand -hex 32` output (startup refuses defaults)
- [ ] `BUSINESS_PROVIDER=google` **only with** `GOOGLE_MAPS_API_KEY` set
- [ ] `CORS_ORIGINS` lists your real frontend origin(s) — no wildcards
- [ ] `FRONTEND_ORIGIN` matches the deployed frontend URL
- [ ] Database credentials changed from `locallead/locallead`
- [ ] `RATE_LIMIT_*` reviewed; loopback exemption left at default (off)
- [ ] `.env` present on the host only — never committed (`git status` clean of it)

## Infrastructure

- [ ] `docker compose up -d --build` — all services healthy
      (`docker compose ps` shows healthy for postgres, redis, api, worker)
- [ ] `curl https://YOURDOMAIN/backend/api/health` →
      `{"checks":{"database":"ok","redis":"ok","workers":"ok","provider":"ok"}}`
- [ ] TLS: place certs, switch nginx to `nginx-tls.conf.example` (HSTS lives
      only there), verify HTTP→HTTPS redirect
- [ ] `PDF_STORAGE_PATH` on a persistent volume; backup policy defined
- [ ] Alembic migration applied: `docker compose exec api alembic upgrade head`
      (the API entrypoint runs it automatically)

## Verification before opening to users

- [ ] One small pilot search with the real provider (`max_results=5`),
      inspecting worker logs for provider errors
- [ ] Register + login + search + export in a browser against production;
      browser console clean
- [ ] Exports download with correct MIME types and open correctly
- [ ] `docker compose exec redis redis-cli pubsub numsub` shows no unexpected
      workers; no host celery/uvicorn processes on the machine
      (`ps aux | grep -E "celery|uvicorn" | grep -v grep` → empty)
- [ ] Admin user bootstrapped (first registered user) and default test
      accounts removed
- [ ] Logs shipping somewhere durable (JSON lines to your aggregator); spot
      check that no secrets appear

## Ongoing

- [ ] Monitor `api_usage` costs weekly; tune `MAX_RESULTS` / rate limits
- [ ] Watch worker healthcheck + `crawl_pages.status` distribution
      (`ssrf_blocked` spikes deserve investigation)
- [ ] Update the container images regularly; re-run
      `backend/scripts/live_e2e.py` after upgrades
