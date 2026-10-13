# LocalLead Intelligence — Developer Handoff

**Commercial lead-generation platform**: search businesses by industry and
location, enrich them with public website contact data, and export branded
PDF / CSV / Excel reports.

> Example: `Hospitals in Porwal Road Pune` → 67 businesses, 61 phones,
> 52 websites, 39 emails (32 high-confidence) → branded PDF report.

This README is written for **the developer taking this project to production**.
Read sections 1–3 before your first deploy; they cover setup, secrets, and the
run commands. Everything else is reference.

> 📖 **Full System & Concepts Documentation**: For an in-depth breakdown of all concepts, architecture diagrams, background pipeline stages, deduplication, SSRF security, and website user journeys, see [**`PROJECT_EXPLANATION.md`**](PROJECT_EXPLANATION.md).
> 🚀 **Hostinger VPS Deployment Guide**: To host this on a Hostinger VPS with 100% free open-source APIs (and switch to Google Maps API later), see [**`docs/HOSTINGER_DEPLOYMENT.md`**](docs/HOSTINGER_DEPLOYMENT.md).

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [First-run setup (5 minutes)](#2-first-run-setup-5-minutes)
3. [Environment variables (`.env`)](#3-environment-variables-env)
4. [Running in development](#4-running-in-development)
5. [Running in production (Docker)](#5-running-in-production-docker)
6. [Enabling real data (Google Places / Serper)](#6-enabling-real-data-google-places--serper)
7. [Tests](#7-tests)
8. [Requested-vs-available data logic](#8-requested-vs-available-data-logic)
9. [Error handling & failure behavior](#9-error-handling--failure-behavior)
10. [Security notes for production](#10-security-notes-for-production)
11. [Repo layout & further docs](#11-repo-layout--further-docs)
12. [Open-source leadgen engine (`leadgen/`)](#12-open-source-leadgen-engine-leadgen)

---

## 1. Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| **Docker + Docker Compose** | 24+ | production stack (recommended) |
| **Python** | 3.11 or 3.12 | backend (local dev without Docker) |
| **Node.js** | 18.17+ (20 LTS recommended) | frontend |
| **PostgreSQL** | 14+ (16 recommended) | production datastore |
| **Redis** | 7+ | job queue + live progress |

Optional (only for local, non-Docker runs): a running Postgres + Redis, or
nothing at all — the backend falls back to SQLite and mock mode.

## 2. First-run setup (5 minutes)

```bash
# 1. Install dependencies
cd backend  &&  pip install -r requirements.txt
cd frontend &&  npm install

# 2. Create your environment file
cp .env.example .env
#    → open .env and set at least SECRET_KEY (see next section)

# 3. Start the full stack (no API keys needed — mock mode)
docker compose up --build
```

| Service | URL |
|---|---|
| Web app (via nginx) | http://localhost:8080 |
| Frontend (direct) | http://localhost:3000 |
| API docs (OpenAPI) | http://localhost:8000/api/docs |
| Health check | http://localhost:8000/api/health |

**First login:** open the app → **Create account**. The first registered user
automatically becomes ADMIN.

## 3. Environment variables (`.env`)

> **Golden rule:** real secrets live **only** in `.env`, which is gitignored.
> `.env.example` is the committed template and documents every variable.
> Never hardcode keys in source files; never commit `.env`.

```bash
cp .env.example .env      # then edit .env
```

### The variables that matter most

| Variable | Required? | Notes |
|---|---|---|
| `SECRET_KEY` | **Yes** | Signs JWTs. Generate: `openssl rand -hex 32`. Production **refuses to boot** with the placeholder value. |
| `DATABASE_URL` | **Yes (prod)** | `postgresql+psycopg2://user:pass@host:5432/dbname` |
| `REDIS_URL` | **Yes (prod)** | `redis://host:6379/0` |
| `BUSINESS_PROVIDER` | Yes | `mock` (no keys needed) · `google` · `serper` |
| `GOOGLE_MAPS_API_KEY` | if provider=google | Google Places API (New) key |
| `SERPER_API_KEY` | if provider=serper | Key from https://serper.dev |
| `CORS_ORIGINS` | **Yes (prod)** | Comma-separated allow-list of your frontend origins |
| `SMTP_*` | optional | Needed only for password-reset emails |

The full annotated list lives in [`.env.example`](.env.example).

### Startup validation (what happens if you forget one)

The backend validates configuration at boot (`backend/app/core/startup_checks.py`):

- **`APP_ENV=production`** — missing keys / placeholder `SECRET_KEY` /
  unconfigured provider ⇒ **the server refuses to start** and prints exactly
  which variable to set:
  ```
  RuntimeError: Refusing to start in production with missing configuration:
    - BUSINESS_PROVIDER=google but GOOGLE_MAPS_API_KEY is missing. ...
  ```
- **Development** — the app still boots (mock mode needs no keys) and prints a
  prominent banner naming the missing variable:
  ```
  ⚠  LocalLead configuration warnings
  BUSINESS_PROVIDER=serper but SERPER_API_KEY is missing. Set it in .env ...
  Fix: edit .env (copy .env.example if it does not exist) and restart.
  ```

If a search is ever started while the configured provider has no key, the job
fails cleanly with a user-safe message — it never crashes the server.

## 4. Running in development

**Backend** (http://localhost:8000, auto-reload)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL=sqlite:///./data/dev.db   # or your Postgres from .env
export BUSINESS_PROVIDER=mock                 # zero-key quickstart
alembic upgrade head                          # create/migrate tables
uvicorn app.main:app --reload --port 8000

# second terminal — background workers (needs Redis):
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO
```

**Frontend** (http://localhost:3000, proxies `/backend/*` → API)

```bash
cd frontend
npm install
npm run dev
```

**Or everything at once with Docker:**

```bash
docker compose up --build      # mock mode works out of the box
```

## 5. Running in production (Docker)

```bash
cp .env.example .env
# edit .env: APP_ENV=production, strong SECRET_KEY, real DATABASE_URL/REDIS_URL
#            (compose overrides these for its internal services), provider keys,
#            CORS_ORIGINS with your real domain, TLS certs under
#            infrastructure/nginx/certs/

docker compose up -d --build
docker compose exec api alembic upgrade head      # apply migrations
docker compose logs -f api worker                 # verify startup checks pass
```

Without Docker, run the same components behind your own supervisor:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO --concurrency=2
cd frontend && npm run build && npm start
```

Production checklist: see [`docs/PRODUCTION_CHECKLIST.md`](docs/PRODUCTION_CHECKLIST.md)
and [`docs/deployment.md`](docs/deployment.md) (TLS, HSTS, non-root containers,
healthchecks, structured logs, backups).

## 6. Enabling real data (Google Places / Serper / OpenStreetMap)

1. Get a **Places API (New)** key (Google Cloud console) and/or a
   [serper.dev](https://serper.dev) key — or use **OpenStreetMap
   (Nominatim)**, which needs **no key at all**.
2. In `.env`:
   ```
   BUSINESS_PROVIDER=google          # or serper — or nominatim (keyless)
   GOOGLE_MAPS_API_KEY=your-key      # and/or SERPER_API_KEY=your-key
   ```

### OpenStreetMap (Nominatim) — the open-source geocoder

Nominatim replaces **all** Google Maps geocoding endpoints:

- `BUSINESS_PROVIDER=nominatim` discovers businesses directly from OSM data
  (amenity/shop/office tags, bounded viewbox searches) — free, keyless, MIT
  licensed data.
- `PROVIDER_FALLBACK_CHAIN=auto` appends Nominatim as a free last resort for
  any non-mock default provider, so an outage of the paid provider
  automatically degrades to OSM instead of failing the search.
- `ENABLE_NOMINATIM_GEOCODE=true` resolves a job's text location (e.g.
  *"Porwal Road, Pune"*) to a lat/lng center via Nominatim before discovery.
- Compliance: honest User-Agent (`NOMINATIM_USER_AGENT`), throttled to
  ≤ 1 request/second, bounded queries — per the
  [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).

### Disify — disposable-email detection (free, keyless)

Website-extracted emails are checked against [disify.com](https://disify.com);
throwaway hosts (mailinator, yopmail, …) are downgraded to `LOW` confidence
and the verdict is stored per contact (`email_disposable`). Disable with
`ENABLE_DISIFY_CHECKS=false`.

### Side-by-side providers (Google + Serper together)

Both keys can be configured at once. Users then choose the data source per
search via the **Data source** dropdown (Auto / Google Places / Serper), and
if the chosen provider fails at runtime (rate limit, outage, bad key) the
next configured provider in `PROVIDER_FALLBACK_CHAIN` serves the search
automatically. The results page clearly states when a fallback was used.

```
PROVIDER_FALLBACK_CHAIN=auto            # default + any other configured provider
PROVIDER_FALLBACK_CHAIN=google,serper   # Google first, Serper as backup
PROVIDER_FALLBACK_CHAIN=serper,google   # Serper first, Google as backup
PROVIDER_FALLBACK_CHAIN=                # fallback disabled
```

Every provider's billable calls are recorded separately in the admin usage
dashboard (`/admin` → Overview), with per-call cost estimates from
`GOOGLE_PLACES_*_COST_PER_CALL`. Google field masks are selective (never
`*`) — only the 13 fields the product uses are requested.

Website crawling (contact enrichment) works with both providers and is on by
default; set `ENABLE_WEBSITE_CRAWLING=false` to disable.

## 7. Tests

```bash
cd backend
./.venv/bin/python -m pytest tests/        # full suite (135 tests)
```

Covers: query parsing, contact normalization, dedup scoring, crawler behavior
against a local HTTP server, SSRF-protection matrix, provider failure scenarios
(timeout, empty, auth errors, malformed payloads), API auth/RBAC/rate limits,
search lifecycle, PDF/CSV/XLSX generation. Live harnesses:
`scripts/live_e2e.py`, `scripts/test_cancel.py`, `scripts/load_test.py` — see
[`docs/TESTING.md`](docs/TESTING.md).

## 8. Requested-vs-available data logic

The product promises: **show exactly what the user asked for — and if that much
is not available, show everything that is, and say so.**

- `POST /api/search` stores both numbers on the job:
  `requested_results` (what the user typed) and `max_results` (the same value
  clamped to the platform cap `MAX_RESULTS`).
- The pipeline compares delivered results against the request at completion and
  writes an explicit `stage_detail`:
  - *Full delivery* → `All 100 requested results delivered.`
  - *Source shortfall* → `Requested 100, showing 45 available — the data source
    returned fewer matches for this query.`
  - *Platform cap* → `Requested 200; platform limit is 60 per search — showing
    all 60 available results.`
- `GET /api/search/{id}/results` returns `requested_results` alongside the
  page data, so the results page always renders an **AvailabilityNotice**:
  > ⓘ Requested 200 leads; the platform limit is 60 per search — showing all
  > 60 available results.
  > ⚠ Requested 100 leads, but only 45 are available for this search — showing
  > all available data. Try broadening the location or industry to find more.
  > ✓ Showing all 45 available results — your full request of 45 was delivered.

Providers already paginate (up to 6 pages of 20 on Google Text Search) and stop
early on exhaustion, so a request for 100 simply returns as many as exist.

## 9. Error handling & failure behavior

**Backend**

- Every provider HTTP call (`google_places.py`, `serper.py`) runs through a
  single `_request()` path with: exponential backoff, `Retry-After` support,
  retry on 429/5xx, immediate failure on 401/403, and typed exceptions
  (`ProviderAuthError`, `ProviderRateLimited`, `ProviderTimeout`,
  `ProviderUnavailable`).
- Provider responses that are not JSON, or have an unexpected shape, are
  converted into clean `ProviderUnavailable` errors; malformed individual place
  records are skipped instead of failing the search.
- Malformed JSON from a provider ⇒ `ProviderUnavailable` with a clear message.
- The pipeline isolates failures: one dead website or one bad record never
  sinks a job; only fatal job-level failures mark the job `failed`, and even
  then the API keeps serving.
- Users see mapped, non-technical messages
  (`providers/base.py → friendly_provider_message`): quota issues read
  *"Business data provider quota has been reached. Please try again later."*

**Frontend**

- `lib/api.ts` maps every HTTP status to an actionable sentence (429 →
  "You're going too fast…", 5xx → "Something went wrong on our side…"),
  treats network dropouts as retryable, and surfaces the backend's own clean
  `detail` when present. Raw stack traces never reach the UI.
- All fetches are wrapped; failures render as toasts/banners, never a crash.

## 10. Security notes for production

- **Secrets**: `.env` is gitignored — verify with `git check-ignore .env`.
  Rotate `SECRET_KEY` on any suspicion of leakage (invalidates sessions).
- **Startup guarantees**: production refuses to boot with placeholder secrets
  or missing provider keys (`PROVIDER_STRICT_CONFIG=true`).
- **Log redaction**: registered secret *values* are scrubbed from structured
  logs (`core/config.py → register_secret`).
- Argon2 password hashing, JWT access tokens (30 min) + rotating refresh
  tokens with reuse detection.
- RBAC: ADMIN vs USER; every job/business/report is ownership-checked.
- SSRF guard on the crawler (`SSRF_PROTECTION=true` — keep it on), robots.txt
  compliance, per-domain rate limiting, honest User-Agent.
- Rate limiting per user (`RATE_LIMIT_*`), strict CORS allow-list, security
  headers + HSTS in production, parameterized SQL everywhere.
- Reports are UUID-named and served only to their owner via authenticated
  downloads.

## 11. Repo layout & further docs

```
├── backend/            FastAPI + SQLAlchemy + Celery + crawler + exports
│   ├── app/api/        REST endpoints (auth, search, business, exports, admin, health)
│   ├── app/providers/  BusinessSearchProvider abstraction + mock/google/serper
│   ├── app/workers/    Celery pipeline stages + progress pub/sub
│   ├── app/core/       config, security, startup checks, rate limiting, logging
│   └── tests/          135 tests (unit, API, crawler, pipeline, exports)
├── frontend/           Next.js 14 + TypeScript + Tailwind
│   ├── app/            routes: login/register/dashboard/results/history/admin
│   ├── lib/api.ts      typed API client: token refresh + friendly error mapping
│   └── components/     UI primitives (badges, toasts, notices, dialogs)
├── infrastructure/     Dockerfiles + nginx reverse proxy (TLS, SSE-safe)
├── leadgen/            Standalone open-source lead-gen engine (CLI, MIT) — see §12
├── docs/               architecture, api, deployment, SECURITY, CRAWLER, TESTING,
│                       TROUBLESHOOTING, PRODUCTION_CHECKLIST
├── docker-compose.yml  postgres + redis + api + worker + frontend + nginx
├── .env.example        every configuration variable, documented
├── requirements.txt    engine dependencies (the platform has backend/requirements.txt)
└── README.md           this file
```

Compliance posture: official data sources only, polite crawling, no fabricated
contacts (every email carries `source_url` + confidence), no scraping of
third-party marketplaces.

License: **MIT** — see [`LICENSE`](LICENSE).

---

## 12. Open-source leadgen engine (`leadgen/`)

A self-contained, modular Python engine implementing the open-source OSINT
lead-generation spec with **zero Google Maps endpoints** and **zero paid
calls required**: it reuses the same compliance ideas as the platform
(robots.txt, SSRF guard, honest User-Agent) but runs standalone on
`requests` + `pandas` + SQLite.

### API stack

| Role | Service | Key? |
|---|---|---|
| Website metadata, emails, social links | **SiteIntel** (Apify actor) or keyless homepage fallback | `APIFY_TOKEN` optional |
| Phone normalization (E.164, `international_format` only) | **Numverify** (apilayer.net) | `NUMVERIFY_API_KEY` optional (falls back to `phonenumbers`) |
| Disposable / free-mail detection | **Disify** (disify.com) | none — keyless |
| Geocoding | **Nominatim** (OpenStreetMap) | none — keyless |
| Regional fallback when OSM has no hit | **Open-Meteo geocoding** | none — keyless |

Copy the engine variables from `.env.example` into your `.env` (all optional:
without keys the engine still runs on its fallback paths).

### Quick start

```bash
pip install -r requirements.txt

# one lead
python -m leadgen run --name "Sunrise Hospital" \
    --website https://sunrisehospital.in --city Pune --country in \
    --phone "+91 98220 12345"

# bulk from CSV (columns: business_name,website,city,country_code,phone,address)
python -m leadgen run --input seeds.csv

# export the SQLite store to output/leads.json and output/leads.csv
python -m leadgen export

# inspect the Crawl Audit Trail
python -m leadgen audit --limit 20
```

### How Email Confidence works

Every extracted email is scored into one of three tiers by
`leadgen/confidence.py` (`calculate_email_confidence`):

| Tier | Condition |
|---|---|
| **HIGH** | The email's domain **matches the business website's domain** (extracted via SiteIntel) — e.g. `info@sunrisehospital.in` found on `sunrisehospital.in`. Subdomains of the business domain also count. |
| **MEDIUM** | The email was found **on the official business website** and its domain is a known free/standard mail host (`gmail.com`, `outlook.com`, …) that Disify reports as **not disposable**. |
| **LOW** | Everything else: emails found on **external pages or third-party directories**, disposable/throwaway hosts (even when found on the official site), or addresses whose provenance is unknown. |

Disify's verdict (`disposable: true|false`, unknown on network failure) is
combined with a local free-host list, so the engine still tier-correctly
when the API is unreachable. This corrects the original spec snippet, whose
`elif is_disposable == False → MEDIUM` branch made `LOW` unreachable for
non-disposable emails and never verified *where* the email was found.

### How the Crawl Audit Trail works

Every outbound HTTP call in the engine goes through `leadgen/audit.py`
(`http_get`), which records a queryable row per attempt — success, failure
or block:

| Field | Meaning |
|---|---|
| `http_status` | HTTP status code (NULL when the request never completed — blocked, DNS failure, timeout). |
| `response_time_ms` | Wall-clock latency of the attempt including retries. |
| `robots_allowed` | Boolean: robots.txt verdict for the target URL and our User-Agent. Disallowed targets are **never fetched** — the row records the refusal. |
| `status_kind` | `ok` / `http_error` / `robots_disallowed` / `ssrf_blocked` / `dns_error` / `ssl_error` / `timeout` / `api_exempt`. |
| `error` | Short failure reason (first 300 chars). |

- **robots.txt**: fetched and cached per origin; a `robots_disallowed` URL
  is skipped before any request hits the target. Documented REST APIs
  (Disify, Numverify, Nominatim, REST Countries, Apify) are exempt from
  robots *enforcement* but still audited with `api_exempt: true`.
- **SSRF guard**: hostname-local names, private/loopback/link-local/metadata
  IP literals and DNS names resolving into private ranges are refused
  (`ssrf_blocked`), including redirect hops.
- **Storage**: rows land in the `crawl_audit_events` SQLite table (also
  embedded per lead as `audit_trail` JSON and summarized in the per-lead
  `crawl_http_status` / `crawl_response_time_ms` / `crawl_robots_allowed`
  columns). View the tail with `python -m leadgen audit`.

### Deduplication (Unique Hash)

`leadgen/dedup.py` computes `unique_hash = sha256(normalized_phone +
"|" + domain)`; the first lead with a given hash is `UNIQUE`, every later
match is flagged `DUPLICATE`. Because phones are normalized to E.164 by
Numverify first, `044 123 456` and `+44 123 456` collapse to the same hash.
Phone-less leads hash on domain alone in a separate namespace so they can
neither collide with nor merge into phone-keyed leads. The index is
pre-seeded from SQLite, so duplicates are caught **across runs**, not just
within one.

### Geocoding (no Google Maps)

`leadgen/geocode.py` resolves **business name + city** through Nominatim
(name+city first, then city alone), and falls back to **Open-Meteo**
city-level geocoding when OSM returns nothing (the originally specified
REST Countries fallback was deprecated by its maintainer and now requires
a paid key, so the engine uses a free, keyless city-level source instead).
Every lead stores the `geo_source` (`nominatim` / `open-meteo`) so
provenance is explicit — and there is not a single `maps.googleapis.com`
reference in the engine (guarded by a test).
