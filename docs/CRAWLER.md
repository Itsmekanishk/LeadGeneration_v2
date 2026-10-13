# Crawler

`app/crawlers/` implements the website contact-discovery stage. It runs only
against websites returned by the business-data provider, stays on each
business's own registrable domain, and is bounded in every dimension.

## Flow

1. Homepage fetch (robots.txt permitting).
2. Candidate pages picked from links: contact / about / footer, mailto hints.
3. Up to `MAX_PAGES_PER_DOMAIN` (default 5) pages per website.
4. Emails (mailto + visible text) and phones (tel: + text) extracted with
   source URL and confidence; duplicates dropped per business.

## Limits (env-tunable)

| Setting | Default | Meaning |
|---|---|---|
| `ENABLE_WEBSITE_CRAWLING` | `true` | master switch |
| `MAX_PAGES_PER_DOMAIN` | `5` | pages per business website |
| `CRAWL_TIMEOUT` | `12` | seconds per page |
| `CRAWL_CONCURRENCY` | `4` | parallel pages per domain |
| `CRAWL_JOB_CONCURRENCY` | `4` | parallel businesses per job |
| `CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC` | `0.5` | ≥2s between same-domain hits |
| `CRAWL_MAX_CONTENT_BYTES` | `2,000,000` | response size cap |
| `SSRF_PROTECTION` | `true` | private/internal address blocking |
| `SSRF_ALLOWED_HOSTS` | *(empty)* | test-rig loopback allowlist |

## Compliance

robots.txt respected; honest User-Agent; no stealth, CAPTCHA solving, proxy
rotation, or access-control bypass. Access denied by a site is recorded as
`robots_disallowed`/`http_error` and the search continues.

## Failure classification (per page, never fatal)

| status | meaning |
|---|---|
| `ok` | fetched, HTML parsed |
| `timeout` | page timed out |
| `robots_disallowed` | robots.txt forbids the path |
| `ssrf_blocked` | URL resolved to private/metadata/internal address |
| `dns_error` | domain does not resolve (dead site) |
| `ssl_error` | TLS handshake/certificate failure |
| `http_error` | 4xx/5xx, non-HTML content type, too many redirects |
| `error` | any other transport failure |

A failed website marks that business "scan failed" in the summary; the job
itself completes — status becomes **completed with partial data** in the UI
when any scans failed or duplicates were merged.

## SSRF guard

Every URL (and every redirect hop) passes `app/utils/url_guard.py`:
scheme allowlist, credential rejection, hostname blocklist, full DNS
resolution check against blocked ranges (loopback, RFC1918, CGNAT,
link-local incl. cloud metadata, IPv6 ULA/link-local/mapped), and
re-validation of each hop. See `docs/SECURITY.md` and
`backend/tests/test_ssrf.py` (70+ cases).
