# Security

## Authentication & sessions

- Passwords hashed with **Argon2** (never stored or logged in plaintext).
- **JWT access tokens** (short-lived) + **rotating refresh tokens** with
  reuse detection; refresh tokens are hashed at rest (`refresh_tokens`).
- First registered user in an empty database becomes **ADMIN** (bootstrap).
- Password reset uses single-use, time-limited tokens (`password_reset_tokens`).
- Login/register are rate-limited per IP; login failure messages do not
  reveal whether the account exists.

## Authorization

- Every job/business/export endpoint is ownership-scoped (`user_id`).
- Cross-user access returns **404** (resource hidden, not "forbidden"), which
  avoids resource enumeration.
- Admin endpoints (`/api/admin/*`) require the ADMIN role → otherwise 403.
- Export downloads re-check ownership on every request (UUID filenames are
  not guessable, but are never treated as authorization).

## SSRF protection (crawler)

All crawler fetches go through `app/utils/url_guard.py` **before any network
request** and re-validate every redirect hop:

1. scheme allowlist: `http`/`https` only (no `file:`, `ftp:`, `data:`).
2. credentials in URL rejected (`user:pass@host`).
3. hostname blocklist: `localhost`, `*.internal`, `*.local`, `*.lan`,
   `*.corp`, cloud metadata hosts (`metadata.google.internal`,
   `169.254.169.254`, …).
4. **DNS resolution checks**: every resolved A/AAAA record must be globally
   routable. Blocked ranges include loopback, RFC1918, CGNAT 100.64/10,
   link-local 169.254/16, 0.0.0.0/8, multicast/reserved, IPv6 unique-local
   `fc00::/7`, link-local `fe80::/10`, IPv4-mapped IPv6, documentation nets.
5. **Redirect re-validation**: redirects are followed hop-by-hop; each
   `Location` is re-checked (blocks "public page redirects into internal
   network" attacks).
6. Any private hit → request refused, status `ssrf_blocked`; unresolvable
   domains are `dns_error` (honest failure classification).

Loopback allowlist: `SSRF_ALLOWED_HOSTS` (comma-separated hostnames) exists
for test rigs only. Default is empty; production keeps full protection.

## Crawler compliance

- robots.txt honored (cached per origin) — `RESPECT_ROBOTS_TXT=true`.
- Honest User-Agent identifying the crawler; no stealth, no CAPTCHA solving,
  no proxy rotation, no auth/paywall bypass.
- Per-domain rate limit (default ≥2s between requests), page caps
  (`MAX_PAGES_PER_DOMAIN`), response size cap, content-type gate
  (HTML/text only), timeout classification, and per-business failure
  isolation: one dead website never fails a job.
- Business discovery uses **official APIs only** (Google Places API (New) or
  the offline mock provider). No search-engine scraping.

## Data integrity

- Email/phone extraction only from provider data and pages fetched from the
  business's own domain; every contact stores `source`, `source_url`,
  `confidence`, `discovered_at`. Nothing is guessed or fabricated.
- Exports neutralize spreadsheet formula injection (cells starting with
  `=`, `+`, `-`, `@` are prefixed with `'`), escape HTML in PDFs, and use
  UUID filenames with ownership-checked downloads.

## Transport & headers

- API and frontend send `Content-Security-Policy` (host-based `connect-src`),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`.
- HSTS is **only** on the HTTPS nginx entrypoint
  (`nginx-tls.conf.example`); the HTTP dev entrypoint never sends it.
- Secrets enter exclusively via environment variables and are validated at
  startup (production refuses default `SECRET_KEY`, refuses `google`
  provider without a key). Secrets are registered for log redaction.

## Logging

Structured JSON logs; registered secret values are redacted before emit;
no API keys, passwords, tokens, or full credentials ever logged.
