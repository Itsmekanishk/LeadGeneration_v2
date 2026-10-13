"""Polite HTTP fetching layer for the website crawler.

Compliance features (deliberately no stealth/anti-detection):
- honors robots.txt (with configurable opt-out for private deployments)
- identifies itself with an honest User-Agent
- per-domain rate limiting (default ≥ 2s between requests to one domain)
- hard timeouts, response-size caps, SSL/DNS error classification
- redirects followed; HTTP errors surfaced as statuses, never exceptions
  that kill a job
"""
from __future__ import annotations

from typing import Optional

import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.utils.url_guard import URLGuard, guard_redirect

log = event_logger("locallead.crawler.fetch")

_ROBOTS_CACHE: dict[str, Optional[RobotFileParser]] = {}
_ROBOTS_LOCK = threading.Lock()
_domain_last_hit: dict[str, float] = {}
_domain_lock = threading.Lock()


@dataclass
class FetchResult:
    """Outcome of one page fetch (never raises for HTTP-level failures)."""

    url: str
    final_url: str = ""
    status: Optional[int] = None
    html: str = ""
    ok: bool = False
    status_kind: str = "error"   # ok | timeout | robots_disallowed | ssrf_blocked | http_error | dns_error | ssl_error | error
    error: str = ""
    duration_ms: int = 0


def _rate_limit_wait(domain: str) -> None:
    """Block until this domain's next allowed request time."""
    settings = get_settings()
    min_interval = settings.crawl_delay_seconds
    with _domain_lock:
        last = _domain_last_hit.get(domain, 0.0)
        now = time.monotonic()
        wait = min_interval - (now - last)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _domain_last_hit[domain] = now


def _get_robots(url: str) -> Optional[RobotFileParser]:
    """Fetch+cache robots.txt for the URL's origin (None when unavailable)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    with _ROBOTS_LOCK:
        if origin in _ROBOTS_CACHE:
            return _ROBOTS_CACHE[origin]
    rp = RobotFileParser()
    rp.set_url(f"{origin}/robots.txt")
    try:
        guard = URLGuard()
        if guard.check(f"{origin}/robots.txt").ok:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                resp = client.get(
                    f"{origin}/robots.txt",
                    headers={"User-Agent": get_settings().crawl_user_agent},
                )
            if resp.status_code == 200 and resp.text:
                rp.parse(resp.text.splitlines())
            else:
                rp = None  # no robots.txt => allowed
        else:
            rp = None  # blocked origin: treat as no robots (fetch itself will refuse)
    except Exception:  # noqa: BLE001 — robots unavailability must not fail crawling
        rp = None
    with _ROBOTS_LOCK:
        _ROBOTS_CACHE[origin] = rp
    return rp


def is_allowed_by_robots(url: str, user_agent: Optional[str] = None) -> bool:
    """True when robots.txt permits fetching *url* for our UA."""
    settings = get_settings()
    if not settings.respect_robots_txt:
        return True
    rp = _get_robots(url)
    if rp is None:
        return True
    try:
        return rp.can_fetch(user_agent or settings.crawl_user_agent, url)
    except Exception:  # pragma: no cover
        return True


def classify_exception(exc: Exception) -> str:
    """Map network exceptions to a status kind for crawl_pages.status."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        msg = str(exc).lower()
        if "name or service not known" in msg or "nodename" in msg or "getaddrinfo" in msg:
            return "dns_error"
        return "error"
    if isinstance(exc, ssl.SSLError):
        return "ssl_error"
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    return "error"


def fetch_page(url: str, *, allow_robots_check: bool = True) -> FetchResult:
    """Fetch one page politely; classifies failures instead of raising.

    Returns a FetchResult whose ``status_kind`` explains any failure:
    ok / timeout / robots_disallowed / http_error / dns_error / ssl_error / error.
    """
    settings = get_settings()
    start = time.monotonic()
    result = FetchResult(url=url)

    if allow_robots_check and settings.respect_robots_txt:
        if not is_allowed_by_robots(url):
            result.status_kind = "robots_disallowed"
            result.error = "Disallowed by robots.txt"
            result.duration_ms = int((time.monotonic() - start) * 1000)
            log.info("robots_disallowed", url=result.url)
            return result

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    _rate_limit_wait(domain)

    guard = URLGuard()
    check = guard.check(url)
    if not check.ok:
        # Distinguish a security block (private/metadata IP) from an ordinary
        # DNS failure — both refuse the fetch, but they mean different things.
        result.status_kind = check.kind if check.kind in ("ssrf_blocked", "dns_error") else "ssrf_blocked"
        result.error = f"blocked by SSRF guard: {check.reason}"
        result.duration_ms = int((time.monotonic() - start) * 1000)
        if result.status_kind == "ssrf_blocked":
            log.warning("fetch_ssrf_blocked", url=url, reason=check.reason)
        else:
            log.info("fetch_dns_failed", url=url, reason=check.reason)
        return result

    current_url = url
    try:
        with httpx.Client(
            timeout=settings.crawl_timeout,
            follow_redirects=False,  # redirects validated hop-by-hop (SSRF)
        ) as client:
            hops = 0
            while True:
                with client.stream(
                    "GET", current_url,
                    headers={
                        "User-Agent": settings.crawl_user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en",
                    },
                ) as resp:
                    # 3xx: validate the next hop before following it
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location", "")
                        resp.close()
                        hops += 1
                        if hops > 5:
                            result.status_kind = "http_error"
                            result.error = "too many redirects"
                            break
                        target = guard_redirect(current_url, location)
                        if target is None:
                            result.status_kind = "ssrf_blocked"
                            result.error = f"redirect to '{location[:200]}' not allowed"
                            break
                        hop_check = guard.check(target)
                        if not hop_check.ok:
                            result.status_kind = hop_check.kind if hop_check.kind in ("ssrf_blocked", "dns_error") else "ssrf_blocked"
                            result.error = f"redirect blocked: {hop_check.reason}"
                            if result.status_kind == "ssrf_blocked":
                                log.warning("fetch_ssrf_blocked", url=target,
                                            reason=hop_check.reason)
                            else:
                                log.info("fetch_dns_failed", url=target,
                                         reason=hop_check.reason)
                            break
                        current_url = target
                        continue
                    result.status = resp.status_code
                    result.final_url = current_url
                    if resp.status_code >= 400:
                        result.status_kind = "http_error"
                        result.error = f"HTTP {resp.status_code}"
                        break
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if ctype and not ctype.startswith(("text/", "application/xhtml")):
                        result.status_kind = "http_error"
                        result.error = f"non-HTML content type '{ctype[:60]}'"
                        break
                    chunks: list[bytes] = []
                    received = 0
                    for chunk in resp.iter_bytes():
                        received += len(chunk)
                        chunks.append(chunk)
                        if received > settings.crawl_max_content_bytes:
                            break
                    result.html = b"".join(chunks).decode("utf-8", errors="replace")
                    result.ok = True
                    result.status_kind = "ok"
                    break
    except Exception as exc:  # noqa: BLE001 — classify, never propagate
        result.status_kind = classify_exception(exc)
        result.error = f"{type(exc).__name__}: {exc}"[:500]
        log.warning("fetch_failed", url=current_url, kind=result.status_kind)
    result.duration_ms = int((time.monotonic() - start) * 1000)
    return result
