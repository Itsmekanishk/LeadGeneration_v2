# API Reference

Interactive OpenAPI docs: **`/api/docs`** (Swagger) and `/api/redoc` once the
API is running. All endpoints are JSON over HTTPS.

## Authentication

`POST /api/auth/register` `{email, password, full_name?}` → `201 TokenResponse`
(first user in an empty system becomes ADMIN).

`POST /api/auth/login` `{email, password}` → `200 TokenResponse`
`POST /api/auth/refresh` `{refresh_token}` → `200 TokenResponse` (rotated)
`POST /api/auth/logout` `{refresh_token}` → `204`
`POST /api/auth/forgot-password` `{email}` → `200 {message, dev_token?}`
(dev_token only outside production without SMTP, for testing)
`POST /api/auth/reset-password` `{token, new_password}` → `200 UserOut`
`GET  /api/auth/me` → `200 UserOut` (Bearer required)

Send `Authorization: Bearer <access_token>` on all protected endpoints.

## Search

### Preview the parser (no job created)
```http
POST /api/search/interpret
{"free_text": "Hospitals in Porwal Road Pune"}
```
```json
{
  "interpreted": {
    "raw_query": "Hospitals in Porwal Road Pune",
    "category": "Hospitals",
    "category_slug": "hospital",
    "location": "Porwal Road, Pune",
    "confidence": "high",
    "notes": []
  },
  "estimated_max_results": 50
}
```

### Create a search job
```http
POST /api/search
{"free_text": "Hospitals in Porwal Road Pune", "max_results": 50}
```
→ `202 {"job_id": "...", "status": "queued"}`

Structured mode: `{"category": "Hospital", "location": "Porwal Road, Pune", ...}`.
Optional: `radius_m`, `country` (ISO-2), `language`, `provider` ("mock"|"google").
Validation errors → `422 {"detail": "Validation failed", "errors": [...]}`.
Rate limit exceeded → `429` + `Retry-After`.

### Track & fetch
- `GET /api/search` — the user's jobs (newest first)
- `GET /api/search/{job_id}` — status + `progress_stats`
- `GET /api/search/{job_id}/stream` — **SSE** progress stream (`data:` events;
  terminal `event: done`)
- `GET /api/search/{job_id}/results?page=&page_size=&q=&has_email=&has_phone=&has_website=&high_confidence_email=&category=&min_rating=&dedup=`
  → paginated businesses
- `GET /api/search/{job_id}/summary` → totals: unique, duplicates removed,
  with phone/website/email, high-confidence emails, failed websites
- `POST /api/search/{job_id}/cancel` → cancels between stages

Statuses: `queued → discovering → enriching → crawling → extracting →
deduplicating → completed | failed | cancelled`.

### Business detail
`GET /api/business/{business_id}` → full record incl. `contacts[]` (each with
`email`, `source_url`, `source`, `confidence`) and `crawl_pages[]` (per-URL
status, durations, counts).

## Exports

Generate (job must be `completed`):
```http
POST /api/search/{job_id}/export/pdf   → {"filename": "report_<uuid>.pdf", ...}
POST /api/search/{job_id}/export/csv
POST /api/search/{job_id}/export/xlsx
```

Download (ownership enforced; UUID filenames):
```http
GET /api/search/{job_id}/export/pdf?filename=report_<uuid>.pdf
```

## Health & Admin

`GET /api/health` → `{status, app_env, checks: {database, redis, workers, provider}}`

Admin (ADMIN role required):
- `GET  /api/admin/jobs?status=` — all users' jobs
- `GET  /api/admin/users` / `PATCH /api/admin/users/{id}` (role, active)
- `GET  /api/admin/usage` — today/month provider calls + estimated cost
- `GET  /api/admin/providers` / `PUT  /api/admin/providers/{name}` — enable /
  set API key (stored encrypted; never returned)
- `GET  /api/admin/audit-logs`

## Error shape

```json
{"detail": "human-readable message"}
```
`401` unauthenticated · `403` insufficient role · `404` not found / not owner ·
`409` job not completed (exports) · `422` validation · `429` rate limited ·
`500` logged server-side, generic message to client.
