# LocalLead Intelligence — Refinement Audit

Status legend: VERIFIED / MOCK VERIFIED / UNIT TESTED / INTEGRATION TESTED / UNVERIFIED / BLOCKED BY MISSING CREDENTIALS / NOT CONFIGURED / FAILED

| Component | Current status | Known issue | Risk | Recommended improvement | Actual change made |
|---|---|---|---|---|---|
| Query parser | UNIT TESTED (all 5 example queries incl. plurals, city retention) | — | Low | — | — |
| Provider abstraction + factory | UNIT TESTED | Google class lazily imported — OK | Low | — | — |
| Google Places provider | UNIT TESTED, UNVERIFIED live (BLOCKED BY MISSING CREDENTIALS) | (a) `except ProviderError` in `get_business_details` references an unimported name → NameError swallows the real error; (b) no friendly error mapping (400/401/429/5xx) for job stage_detail | Medium | Import ProviderError; map exceptions to user-friendly messages | (a) added `ProviderError` import; (b) added `_friendly_provider_message()` used by pipeline |
| Crawler fetch layer (`polite_fetcher`) | UNIT TESTED vs local HTTP server; UNVERIFIED against real internet | **No SSRF protection**: fetches any URL — localhost, private RFC1918 ranges, link-local, cloud metadata (169.254.169.254); redirects followed blindly into internal networks; no content-type check (binary downloads) | **High** | Validate scheme + resolve host + block private/loopback/link-local/CGNAT/unique-local IPs, re-validate each redirect hop, check Content-Type | Added `app/utils/url_guard.py` (URLGuard: allowlist scheme http/https, hostname blocklist incl. `metadata.google.internal`, DNS resolution checks for all A/AAAA records, per-hop re-check) wired into `fetch_page` + robots fetch; `ssrf_blocked` status kind; redirect hop validation via `event_hooks`; Content-Type must be text/* or empty |
| Contact finder | UNIT TESTED | In-process phone dedup re-normalizes repeatedly (minor CPU waste) | Low | Cache normalization | — (kept; correctness fine) |
| Contact normalizer | UNIT TESTED | `normalize_phone` exception path can mask message | Low | — | — |
| Deduplication (DuplicateResolver) | UNIT TESTED (merge cases incl. same-name/diff-address) | — | Low | Regression tests added | Added regression tests (same phone diff business not merged; same website slight name variants merged) |
| Pipeline (`workers/pipeline.py`) | INTEGRATION TESTED (39 live checks + browser) | Provider failure message leaks raw exception to stage_detail | Medium | Friendly provider messages | Uses `_friendly_provider_message()`; raw detail kept in `error_message` server-side only, stage_detail user-safe |
| Celery tasks | INTEGRATION TESTED (real broker) | No cancellation checks between stages | Low | Add `is_job_cancelled()` checks | Added cancellation check between crawl batches — cancelled jobs stop early |
| Progress (SSE) | INTEGRATION TESTED (10 live events through nginx) | Keep-alive path re-reads cached `db.get(SearchJob)` — SQLAlchemy identity-map may serve a stale object → stream could hang if events missed | Medium | `db.expire_all()` before re-read | Added `db.expire_all()` in SSE keep-alive loop |
| Auth (JWT + refresh rotation) | INTEGRATION TESTED (401s, rotation, reuse detection) | — | Low | — | — |
| Authorization / IDOR | INTEGRATION TESTED (cross-user 404, USER→admin 403) | Export download allows admin override — intended | Low | — | — |
| Rate limiting | INTEGRATION TESTED | — | Low | — | — |
| Security headers | PARTIAL — no CSP | Missing Content-Security-Policy | Medium | Add CSP via headers middleware | Added conservative CSP (`default-src 'self'`; script/style/img/connect allowances for Next.js inline requirements) |
| Export service (file security) | INTEGRATION TESTED (UUID names, ownership) | `resolve_download` traversal check OK; admin path re-queries without user filter — fine | Low | — | — |
| PDF export | UNIT TESTED (1..120 rows) | Html-escape: business names with `<`/`&` could break Paragraph markup | Medium | Escape all interpolated text | All user-derived PDF text now escaped via `html.escape` before Paragraph; added regression test |
| CSV export | UNIT TESTED | **Formula injection**: cells starting with `=`/`+`/`-`/`@` execute in Excel | Medium-High | Prefix `'` on formula-leading cells | `build_csv` sanitizes formula-leading cells; Unicode/quoting tests added |
| XLSX export | UNIT TESTED | **Formula injection** in openpyxl (`=` strings become live formulas); no hyperlinks | Medium | Hyperlink cells for Website; neutralize formula-leading strings | `_safe_cell` guard + Website hyperlink cells; Unicode/long-name/500-row tests added |
| Exports API | INTEGRATION TESTED | — | Low | — | — |
| Database (12 tables) | INTEGRATION TESTED (Postgres migration applied) | Audited: hot-path indexes already present in initial schema (27 create_index ops incl. `ix_search_jobs_user_status`, `ix_businesses_job_name`, `ix_contacts_business`) | Low | None — query patterns covered | Confirmed no extra migration needed |
| Docker / nginx | INTEGRATION TESTED (6 containers healthy after conf.d fix) | Worker container had no healthcheck | Low | healthcheck on worker (celery inspect ping) | Added `celery inspect ping` healthcheck to worker service in docker-compose.yml (compose config validated) |
| Frontend core | BROWSER VERIFIED (register→search→results→exports→logout, 0 console errors) | `useJobStream` polling-fallback interval leaks on unmount (cleanup returned inside async catch) | Medium | hoist interval ref + clear in effect cleanup | Fixed interval lifecycle; regression-safe |
| Frontend empty/error states | PARTIAL | Results/history pages show blank on error | Medium | Error states wired to ApiError messages | Error states + retry action added to results/history |
| Mock mode separation | VERIFIED | Provider badge exists | Low | — | — |
| Startup guards | UNIT TESTED | CSP/CORS defaults fine for dev | Low | Production CORS guard exists in config | — |
| SMTP | NOT CONFIGURED | No credentials available | — | Report as UNVERIFIED | — |
| TLS | NOT CONFIGURED | No certs available | — | Report as UNVERIFIED | — |
| Real Google pilot | BLOCKED BY MISSING CREDENTIALS | No API key in env | — | Report as UNVERIFIED | — |

Change set summary (fix order per master prompt):

1. **AUDIT** — this document.
2. **BASELINE** — pytest 55/55, live_e2e 39/39, next build: green before changes.
3. **BACKEND** — friendly provider errors; pipeline stage_detail safety; Celery cancellation checks; SSE staleness fix.
4. **CRAWLER + SSRF** — `url_guard` with DNS-level private-IP blocking incl. redirect re-validation + content-type gate; dedicated SSRF test suite.
5. **GOOGLE PROVIDER** — ProviderError import fix; friendly error mapping; retry/backoff unchanged (already correct).
6. **AUTH/SECURITY** — CSP header; (CORS, rate limits, file security already verified).
7. **EXPORTS** — CSV formula-injection sanitization; PDF html-escaping + None-safe fields; XLSX formula-injection guard + hyperlinks; Unicode/long-name/500-row scale tests.
8. **UI POLISH** — SSE hook leak fix; error states; (design already verified in browser).
9. **DOCKER/NGINX** — worker healthcheck; re-verified compose + containers.
10. **E2E** — full stack re-run + browser flow with clean console.
11. **REAL GOOGLE TEST** — skipped; no credentials (UNVERIFIED).
12. **FINAL REPORT** — docs/FINAL_PRODUCTION_READINESS.md.
