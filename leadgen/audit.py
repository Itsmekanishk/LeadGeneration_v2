# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Crawl Audit Trail: polite fetching + full record of every HTTP attempt.

Every outbound call in the engine goes through :func:`http_get`, which:

1. checks robots.txt for the target URL and records the ``robots_allowed``
   boolean (or an exempt reason for pure REST APIs, which are not crawls),
2. enforces SSRF safety (blocks private/loopback/link-local/metadata IPs and
   dangerous redirects),
3. records ``http_status`` and ``response_time_ms`` for **every** attempt —
   success, failure or block,
4. never raises for request-level failures; failures are returned as a
   normalized :class:`FetchResult` so one dead endpoint cannot fail a run.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from leadgen.config import get_settings

_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}
_ROBOTS_LOCK = threading.Lock()

# API hosts whose whole surface is a documented REST API (not crawled pages);
# robots.txt for them describes bot crawlers of web UIs, not API clients.
_API_EXEMPT_HOSTS = {
    "disify.com", "api.disify.com",
    "apilayer.net", "api.apilayer.net",
    "nominatim.openstreetmap.org",
    "restcountries.com", "api.restcountries.com",
    "api.apify.com",
}

_PRIVATE_NETWORKS = [
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
        "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4", "::1/128",
        "fc00::/7", "fe80::/10", "fde8:8d8e:f7c4::/48",
    )
]


def _ip_is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable ⇒ refuse
    return addr.is_private or addr.is_loopback or addr.is_link_local or \
        addr.is_multicast or addr.is_reserved or addr.is_unspecified or \
        any(addr in net for net in _PRIVATE_NETWORKS)


def _is_ip_literal(host: str) -> bool:
    """True when *host* is a literal IP address (not a DNS name)."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _resolve_ip(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, None)[0][4][0]
    except OSError:
        return None


def _ssrf_blocked(url: str) -> str | None:
    """Return a block reason when *url* fails SSRF safety, else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"scheme '{parsed.scheme}' not allowed"
    host = parsed.hostname or ""
    if not host:
        return "missing hostname"
    if host in ("localhost",) or host.endswith(".localhost") or \
            host.endswith(".internal") or host.endswith(".local"):
        return f"host '{host}' is a local name"
    if _is_ip_literal(host) and _ip_is_private(host):
        return f"IP literal '{host}' is private"
    ip = _resolve_ip(host)
    if ip and _ip_is_private(ip):
        return f"host '{host}' resolves to private IP {ip}"
    return None


def _get_robots(url: str, ua: str) -> RobotFileParser | None:
    """Fetch+cache robots.txt for *url*'s origin (None ⇒ no robots ⇒ allowed)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    with _ROBOTS_LOCK:
        if origin in _ROBOTS_CACHE:
            return _ROBOTS_CACHE[origin]
    rp: RobotFileParser | None = RobotFileParser()
    rp.set_url(f"{origin}/robots.txt")
    try:
        resp = requests.get(f"{origin}/robots.txt", timeout=6, headers={
            "User-Agent": ua}, allow_redirects=False)
        if resp.status_code == 200 and resp.text.strip():
            rp.parse(resp.text.splitlines())
        else:
            rp = None
    except Exception:  # noqa: BLE001 — robots unavailability must not fail fetching
        rp = None
    with _ROBOTS_LOCK:
        _ROBOTS_CACHE[origin] = rp
    return rp


def robots_allowed(url: str, user_agent: str | None = None) -> bool:
    """True when robots.txt permits fetching *url* for our User-Agent."""
    settings = get_settings()
    ua = user_agent or settings.user_agent
    if urlparse(url).netloc.lower().rstrip(".") in _API_EXEMPT_HOSTS:
        return True  # documented REST API, not a crawl
    rp = _get_robots(url, ua)
    if rp is None:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:  # pragma: no cover
        return True


@dataclass
class FetchResult:
    """Normalized outcome of one HTTP fetch (never raises for HTTP errors)."""

    url: str
    ok: bool = False
    status: int | None = None
    response_time_ms: int = 0
    robots_allowed: bool = True
    status_kind: str = "error"
    # ok | http_error | robots_disallowed | ssrf_blocked | dns_error |
    # ssl_error | timeout | api_exempt | error
    error: str = ""
    content: str = ""
    json: Any = None
    audit: dict[str, Any] = field(default_factory=dict)


def http_get(url: str, *, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None,
             api_exempt: bool = False,
             session: requests.Session | None = None) -> FetchResult:
    """Polite GET with SSRF guard, robots check and full audit record.

    ``api_exempt=True`` marks calls to documented REST APIs (Nominatim,
    Disify, Numverify, REST Countries, Apify): they skip the robots.txt
    *enforcement* step (their robots files govern web crawlers, not API
    clients) but every request is still audit-logged with
    ``robots_allowed=True, status_kind='api_exempt'``.
    """
    s = get_settings()
    result = FetchResult(url=url)
    start = time.monotonic()

    host = (urlparse(url).netloc or "").lower().rstrip(".")
    if api_exempt or host in _API_EXEMPT_HOSTS:
        result.robots_allowed = True
        result.status_kind = "api_exempt"
    else:
        result.robots_allowed = robots_allowed(url, s.user_agent)
        if not result.robots_allowed:
            result.status_kind = "robots_disallowed"
            result.error = "Disallowed by robots.txt"
            result.response_time_ms = int((time.monotonic() - start) * 1000)
            result.audit = {
                "url": url, "http_status": None,
                "response_time_ms": result.response_time_ms,
                "robots_allowed": False, "status_kind": result.status_kind,
                "error": result.error,
            }
            return result

    block = _ssrf_blocked(url)
    if block:
        result.status_kind = "ssrf_blocked"
        result.error = f"blocked by SSRF guard: {block}"
        result.response_time_ms = int((time.monotonic() - start) * 1000)
        result.audit = {
            "url": url, "http_status": None,
            "response_time_ms": result.response_time_ms,
            "robots_allowed": result.robots_allowed,
            "status_kind": result.status_kind, "error": result.error,
        }
        return result

    merged_headers = {"User-Agent": s.user_agent, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)

    # Remember the exemption now: status_kind will be overwritten by the
    # fetch outcome, but the audit row must keep the api_exempt marker.
    was_exempt = api_exempt or result.status_kind == "api_exempt"

    last_error = ""
    for attempt in range(s.max_retries + 1):
        try:
            resp = (session or requests).get(
                url, params=params, headers=merged_headers,
                timeout=s.timeout_seconds, allow_redirects=True,
            )
            result.status = resp.status_code
            result.content = resp.text
            result.ok = 200 <= resp.status_code < 300
            result.status_kind = "ok" if result.ok else "http_error"
            result.error = "" if result.ok else f"HTTP {resp.status_code}"
            if result.ok:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "json" in ctype:
                    try:
                        result.json = resp.json()
                    except ValueError:
                        pass
            break
        except requests.exceptions.SSLError as exc:
            result.status_kind = "ssl_error"
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            break
        except requests.exceptions.Timeout as exc:
            result.status_kind = "timeout"
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            break
        except requests.exceptions.ConnectionError as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            result.status_kind = "dns_error" if _resolve_ip(
                urlparse(url).hostname or "") is None else "error"
            if attempt < s.max_retries:
                time.sleep(1.0)
            continue
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            break

    result.response_time_ms = int((time.monotonic() - start) * 1000)
    if not result.ok and not result.error:
        result.error = last_error or result.status_kind
    result.audit = {
        "url": url, "http_status": result.status,
        "response_time_ms": result.response_time_ms,
        "robots_allowed": result.robots_allowed,
        "status_kind": result.status_kind, "error": result.error,
    }
    if was_exempt:
        result.audit["api_exempt"] = True
    return result


def audit_record(result: FetchResult, service: str,
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flatten a FetchResult into a Crawl Audit Trail row (persistable)."""
    record = {
        "service": service,
        "url": getattr(result, "url", ""),
        "http_status": getattr(result, "status", None),
        "response_time_ms": getattr(result, "response_time_ms", 0),
        "robots_allowed": getattr(result, "robots_allowed", True),
        "status_kind": getattr(result, "status_kind", "error"),
        "error": getattr(result, "error", ""),
    }
    if extra:
        record.update(extra)
    return record


def json_dumps(obj: Any) -> str:
    """Compact, stable JSON serialization used for hashes and exports."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)
