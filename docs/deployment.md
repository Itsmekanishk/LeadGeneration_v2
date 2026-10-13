# Deployment Guide

## 1. Prerequisites

- Docker Engine 24+ & Compose v2
- A domain with DNS pointed at the host
- (Production data) Google Places API (New) key with billing enabled
- TLS certificates (Let's Encrypt recommended)

## 2. Configure

```bash
cp .env.example .env
```

Mandatory production changes:

| Variable | Requirement |
|---|---|
| `APP_ENV` | `production` (enables HSTS, strict cookie/session posture) |
| `SECRET_KEY` | long random value (`openssl rand -hex 32`) — startup **fails** on defaults |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `BUSINESS_PROVIDER` | `google` (requires `GOOGLE_MAPS_API_KEY`) or `mock` for staging |
| `CORS_ORIGINS` | your exact frontend origin(s), no wildcards |
| `FRONTEND_ORIGIN` | same origin |
| Postgres/Redis passwords | change from defaults in compose override |

Tune operational knobs as needed: `MAX_RESULTS`, `MAX_PAGES_PER_DOMAIN`,
`CRAWL_TIMEOUT`, `CRAWL_CONCURRENCY`, `CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC`,
`RATE_LIMIT_SEARCH_PER_MIN`, `RATE_LIMIT_API_PER_MIN`, cost-per-call estimates.

## 3. TLS with nginx

By default nginx serves plain HTTP on port 8080 so local runs work with zero
setup. For production TLS:

1. Place certificates in `infrastructure/nginx/certs/`:

   ```
   certs/fullchain.pem
   certs/privkey.pem
   ```

   Let's Encrypt example:

   ```bash
   sudo certbot certonly --standalone -d lead.example.com
   cp /etc/letsencrypt/live/lead.example.com/fullchain.pem infrastructure/nginx/certs/
   cp /etc/letsencrypt/live/lead.example.com/privkey.pem  infrastructure/nginx/certs/
   ```

2. Switch nginx to the TLS config:

   ```bash
   cp infrastructure/nginx/nginx-tls.conf.example infrastructure/nginx/nginx.conf
   docker compose restart nginx
   ```

   This serves HTTPS on 8443 and redirects HTTP 8080 → HTTPS.

## 4. Launch

```bash
docker compose up -d --build
docker compose ps
curl -s localhost:8080/backend/api/health | jq
```

The `api` service runs `alembic upgrade head` before serving; migrations are
idempotent. Worker scaling:

```bash
docker compose up -d --scale worker=4
```

## 5. Create the first (admin) user

Register via the UI or API — the **first user on an empty system becomes
ADMIN**. Then immediately create normal USER accounts for staff.

## 6. Operations

- **Logs**: JSON lines on stdout (`docker compose logs -f api worker`),
  secret values redacted. Ship to your aggregator of choice.
- **Health**: `GET /api/health` covers DB, Redis, Celery worker presence,
  provider configuration. Wire to your uptime monitor.
- **Backups**: `pgdata` volume (Postgres) + `reports` volume (generated
  reports). Example: `docker compose exec postgres pg_dump -U locallead locallead | gzip > backup.sql.gz`
- **Usage control**: admin dashboard (`/admin`) shows today/month provider
  calls and estimated cost; per-call cost constants are env-configurable.
- **Upgrades**: `git pull && docker compose up -d --build`; run
  `docker compose exec api alembic upgrade head` if new migrations exist.

## 7. Hardening checklist

- [ ] `SECRET_KEY` rotated & stored in a secret manager; `ENCRYPTION_KEY` set
- [ ] Postgres/Redis passwords rotated; ports not exposed publicly
      (remove `ports:` for postgres/redis/api in a prod override)
- [ ] TLS working; HTTP redirects to HTTPS; HSTS present
- [ ] `CORS_ORIGINS` exact-match list
- [ ] Rate limits reviewed (`RATE_LIMIT_*`)
- [ ] Crawler UA/contact URL points to a real contact page
- [ ] Backups scheduled and restore tested
- [ ] Provider key restricted by referrer/API restrictions in Google console
