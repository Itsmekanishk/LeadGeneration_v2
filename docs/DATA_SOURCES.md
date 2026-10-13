# Data Sources

## Business discovery (primary source)

| Provider | Status | Notes |
|---|---|---|
| **Google Places API (New)** | code UNIT/INTEGRATION TESTED; **live API UNVERIFIED — BLOCKED BY MISSING CREDENTIALS** | official paid API; selective field masks; exponential backoff on 429/5xx; per-call usage accounting into `api_usage`; enabled via `BUSINESS_PROVIDER=google` + `GOOGLE_MAPS_API_KEY` |
| **MockBusinessProvider** | VERIFIED | deterministic offline data for development/tests; fault-injection scenarios; **never** auto-selected in production |

Provider selection is environment-driven and resolved behind the
`BusinessSearchProvider` abstraction (`app/providers/base.py`); swapping
providers requires no application changes. Startup fails loudly in
production if the configured provider's credentials are missing.

## Website enrichment (secondary source)

The business's **own public website** is scanned politely (see
`docs/CRAWLER.md`) for publicly displayed contact details. Only pages on the
business's own registrable domain are considered; robots.txt is honored.

## What data is collected

- Provider fields: name, category, address, phone, website, rating, review
  count, latitude/longitude, opening hours (as contractually usable),
  provider place id + URL.
- Website fields: publicly displayed emails and phone numbers, each with
  source URL, source type, and confidence (`HIGH` = official contact page,
  `MEDIUM` = other official page, `LOW` = less authoritative public page).

## What is never done

- No guessed/generated emails (pattern prediction is not implemented).
- No scraping of search engines, social networks, or paywalled/protected
  sources; no CAPTCHA, auth, or rate-limit bypass; no stealth crawling.
- No collection beyond publicly displayed business contact information.

## Provenance

Every enriched contact keeps `email/phone`, `source` (provider/website),
`source_url`, `confidence`, and `discovered_at`, and the results UI plus all
three export formats surface these fields (CSV columns `Email Confidence`,
`Phone Source`, `Website Source`; PDF shows confidence under each email).

## Usage & cost accounting

Every provider call is written to `api_usage` (provider, endpoint, count,
estimated cost, user, job, timestamp). Admin → Usage shows today/month
requests and estimated cost, based on configurable per-call cost estimates
(`GOOGLE_PLACES_*_COST_PER_CALL`).
