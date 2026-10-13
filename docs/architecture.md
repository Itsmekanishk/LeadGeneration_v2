# Architecture

## System overview

```
┌──────────────┐   HTTPS   ┌─────────┐    /backend/*    ┌───────────────┐
│   Browser    │──────────▶│  nginx  │─────────────────▶│  FastAPI API  │
└──────────────┘           └─────────┘                  │  (uvicorn)    │
                              │                        └──────┬────────┘
                              │ /                    │        │
                              ▼                      │        │
                       ┌─────────────┐               │        │
                       │  Next.js    │               ▼        ▼
                       │  frontend   │        ┌─────────┐ ┌──────────┐
                       └─────────────┘        │  Redis  │ │ Postgres │
                                              └────┬────┘ └────▲─────┘
                                                   │           │
                                                   ▼           │
                                            ┌──────────────┐   │
                                            │ Celery worker│───┘
                                            │ (pipeline)   │
                                            └───┬──────┬───┘
                                                │      │
                            ┌───────────────────┘      └──────────────┐
                            ▼                                         ▼
                  ┌──────────────────┐                    ┌────────────────────┐
                  │ Business provider│                    │ Business websites  │
                  │ (Places API/mock)│                    │ (polite crawler)   │
                  └──────────────────┘                    └────────────────────┘
```

## Search pipeline (one Celery task per job)

| Stage | Status | What happens |
|---|---|---|
| 1 | `discovering` | Query parsed → `BusinessSearchProvider.search_businesses()` via selective fields |
| 2 | `enriching` | Normalize names/URLs/phones, persist `businesses` rows (unique per job+place_id) |
| 3 | `crawling` | Parallel (default 4) website scans: homepage → contact/about links, ≤ `MAX_PAGES_PER_DOMAIN`, robots.txt + per-domain rate limit |
| 4 | `extracting` | mailto/text email extraction with confidence; tel/text phones; persist `business_contacts`, `crawl_pages` |
| 5 | `deduplicating` | `DuplicateResolver`: strong ids (normalized website/phone) merge; name+address fuzzy matching; canonical row kept |
| 6 | `completed` | Counters (total/completed/failed/emails/phones/websites) published |

Per-business failures (timeout, 403/404/429/5xx, DNS, SSL, malformed HTML) are
isolated: recorded in `crawl_pages.status` and the job continues. Fatal
provider errors mark the job `failed` with a message; nothing crashes silently.

## Provider abstraction

```
BusinessSearchProvider (abc)
├── search_businesses(PlaceSearchQuery) -> list[PlaceDetails]
├── get_business_details(place_id) -> PlaceDetails | None
└── health_check() -> bool
```

- `MockBusinessProvider` — deterministic, offline, fault-injection scenarios
  (`timeout`, `empty` in location text) used by tests and demos.
- `GooglePlacesProvider` — official Places API (New); selective field masks;
  exponential backoff on 429/5xx/network; `api_usage` rows recorded per call.

The pipeline depends only on the interface + factory (`get_provider(name)`).
Adding Outscraper/HERE/etc. = one new class + factory registration.

## Crawler compliance model

- `robots.txt` fetched+cached per origin; disallowed URLs recorded as
  `robots_disallowed` (configurable opt-out for private deployments).
- Honest UA: `LocalLeadBot/1.0 (+contact; compliant crawler)`.
- Token-bucket-ish per-domain limiter: ≥ `CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC`
  (default 0.5 ⇒ ≥2s between same-domain requests).
- Hard page timeout (`CRAWL_TIMEOUT`), streamed size cap
  (`CRAWL_MAX_CONTENT_BYTES`), bounded queue (≤ `MAX_PAGES_PER_DOMAIN`).
- No JS execution needed for contact extraction in v1 (httpx + BeautifulSoup
  over server-rendered HTML). Playwright is intentionally unused by default:
  only if a future provider contract requires rendering, and never to evade
  bot defenses.

## Data model (core tables)

`users`, `search_jobs`, `businesses` (denormalized best contact: email,
email_confidence, phone_normalized, phone_source), `business_contacts`
(every finding + provenance), `crawl_pages` (per-URL outcome), `crawl_events`
(domain-level events), `exports` (UUID filenames + ownership),
`api_usage` (provider calls + estimated cost), `audit_logs`,
`refresh_tokens`, `password_reset_tokens`, `provider_configs`.

## Progress delivery

Workers `update_job()` → Postgres (durable) + Redis pub/sub `locallead:job_events`
(live). API exposes `GET /api/search/{id}/stream` (SSE) that replays the last
state, subscribes, and falls back to keep-alives + DB polling so a missed event
never strands the UI. Frontend `useJobStream` consumes SSE with polling fallback.

## Security architecture

- AuthN: JWT access (30 min) + rotating refresh (7 d, reuse detection).
  Passwords: Argon2 (bcrypt fallback). Reset: single-use hashed tokens, TTL 30 min.
- AuthZ: `CurrentUser` / `AdminUser` deps; every job/business/export check
  ownership (owner or admin). Admin endpoints under `/api/admin/*`.
- Inputs: Pydantic validation on every endpoint; SQLAlchemy bound params only.
- Rate limits: login/register/forgot (IP), search creation (user), general API.
- Transport: nginx TLS termination; HSTS in production; security headers on
  both app layers.
- Secrets: env-only, redacted in structured logs (`SecretsFilter` scrubs
  registered secret values; sensitive keys masked in extras).
