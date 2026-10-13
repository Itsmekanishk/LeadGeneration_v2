"""SSRF protection for the website crawler.

The crawler fetches URLs that originate from business-provider data (semi-
trusted) and from links parsed out of crawled pages (untrusted). Before any
network request is made, every URL must pass :class:`URLGuard`:

1. scheme must be http/https
2. hostname must not be a literal IP in a blocked range or a blocked name
   (localhost, *.internal, *.local, cloud metadata hostnames, ...)
3. every resolved A/AAAA address must be globally routable (public)
4. redirects are followed one hop at a time; each hop's Location is
   re-validated before the next request, so a public page cannot redirect
   us into an internal network.

Blocked ranges (IPv4 + IPv6): loopback, private (RFC1918), link-local
(169.254/16 — includes AWS/GCP/Azure metadata at 169.254.169.254),
CGNAT 100.64/10, 0.0.0.0/8, multicast, reserved, IPv6 unique-local fc00::/7,
link-local fe80::/10, IPv4-mapped IPv6, and documentation ranges.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


def get_allowed_hosts() -> frozenset[str]:
    """Read the SSRF allowlist from settings (imported lazily to avoid a cycle)."""
    try:
        from app.core.config import get_settings

        return get_settings().ssrf_allowed_host_set
    except Exception:  # pragma: no cover — settings unavailable in some tooling
        return frozenset()


_BLOCKED_HOSTNAME_SUFFIXES = (
    ".internal", ".local", ".localhost", ".lan", ".home", ".corp",
)
_BLOCKED_HOSTNAMES = {
    "localhost", "metadata", "metadata.google.internal", "instance-data",
    "instance-data.ec2.internal", "169.254.169.254",
}

# Precomputed blocked networks (module-level; computed once).
_v4 = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),      # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("169.254.0.0/16"),     # link-local + cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),      # benchmarking
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/3"),        # multicast + reserved
]
_v6 = [
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),             # unspecified
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped
    ipaddress.ip_network("64:ff9b:1::/48"),     # NAT64 local
    ipaddress.ip_network("100::/64"),
    ipaddress.ip_network("2001:db8::/32"),      # documentation
    ipaddress.ip_network("fc00::/7"),           # unique local
    ipaddress.ip_network("fe80::/10"),          # link local
    ipaddress.ip_network("ff00::/8"),           # multicast
]


def is_blocked_ip(ip_str: str) -> bool:
    """True when *ip_str* is not a globally-routable public address."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped  # ::ffff:10.0.0.5 → 10.0.0.5
    networks = _v6 if addr.version == 6 else _v4
    return any(addr in net for net in networks)


def _hostname_blocked(hostname: str, allowed_hosts: frozenset[str] | None = None) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if allowed_hosts and host in allowed_hosts:
        return False
    if host in _BLOCKED_HOSTNAMES:
        return True
    if host.endswith(_BLOCKED_HOSTNAME_SUFFIXES):
        return True
    # literal IPs are checked via is_blocked_ip
    try:
        ipaddress.ip_address(host)
        return is_blocked_ip(host)
    except ValueError:
        return False


def resolve_hostname(hostname: str) -> list[str]:
    """Resolve *hostname* to all A/AAAA addresses (empty on failure)."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, UnicodeError):
        return []
    out: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in out:
            out.append(addr)
    return out


@dataclass
class UrlCheck:
    """Result of validating a URL before fetching.

    ``kind`` distinguishes a security block (``ssrf_blocked``) from an
    ordinary DNS failure (``dns_error``) so crawls classify honestly.
    """

    ok: bool
    reason: str = ""
    kind: str = "ssrf_blocked"  # ok | ssrf_blocked | dns_error
    resolved_ips: list[str] | None = None


class URLGuard:
    """Validate URLs against SSRF rules; safe to reuse across requests."""

    def __init__(self, *, resolve_dns: bool = True, allowed_hosts: frozenset[str] | None = None) -> None:
        self.resolve_dns = resolve_dns
        # Explicit allowlist beats DNS/private-IP blocking (test rigs only).
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else get_allowed_hosts()

    def check(self, url: str) -> UrlCheck:
        """Full validation for a URL about to be fetched."""
        try:
            parsed = urlparse(url)
        except ValueError:
            return UrlCheck(ok=False, reason="unparseable url")
        if parsed.scheme not in ("http", "https"):
            return UrlCheck(ok=False, reason=f"scheme '{parsed.scheme}' not allowed")
        hostname = parsed.hostname or ""
        if not hostname:
            return UrlCheck(ok=False, reason="missing hostname")
        if parsed.username or parsed.password:
            return UrlCheck(ok=False, reason="credentials in url not allowed")
        if _hostname_blocked(hostname, self.allowed_hosts):
            return UrlCheck(ok=False, reason=f"hostname '{hostname}' is blocked")
        if self.allowed_hosts and hostname in self.allowed_hosts:
            return UrlCheck(ok=True, kind="ok")
        if not self.resolve_dns:
            return UrlCheck(ok=True, kind="ok")
        ips = resolve_hostname(hostname)
        if not ips:
            return UrlCheck(ok=False, reason="dns resolution failed", kind="dns_error")
        for ip in ips:
            if is_blocked_ip(ip):
                return UrlCheck(
                    ok=False,
                    reason=f"resolves to blocked address {ip}",
                    kind="ssrf_blocked",
                    resolved_ips=ips,
                )
        return UrlCheck(ok=True, kind="ok", resolved_ips=ips)


def guard_redirect(previous_url: str, location_header: str) -> str | None:
    """Return the absolute redirect target if it may be followed, else None.

    Applies the same scheme/host rules to the hop target. The caller then
    runs :meth:`URLGuard.check` (DNS validation) on the result before
    requesting it.
    """
    try:
        from urllib.parse import urljoin

        target = urljoin(previous_url, (location_header or "").strip())
        parsed = urlparse(target)
        if parsed.scheme not in ("http", "https"):
            return None
        if _hostname_blocked(parsed.hostname or "", get_allowed_hosts()):
            return None
        return target
    except ValueError:
        return None
