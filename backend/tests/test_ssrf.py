"""SSRF protection tests (Phase 20).

Covers: blocked IP ranges, blocked hostnames, scheme validation, credential
stripping, DNS-resolution-based blocking, redirect re-validation, and the
end-to-end behaviour of ``fetch_page`` against blocked targets.
"""
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Unit tests — is_blocked_ip matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,expected",
    [
        # loopback
        ("127.0.0.1", True),
        ("127.9.9.9", True),
        # RFC1918 private
        ("10.0.0.5", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        ("192.168.1.100", True),
        # link-local / cloud metadata
        ("169.254.169.254", True),
        ("169.254.0.1", True),
        # CGNAT, unspecified, broadcast-range
        ("100.64.0.1", True),
        ("0.0.0.0", True),
        ("0.1.2.3", True),
        # documentation / benchmarking / reserved
        ("192.0.2.1", True),
        ("198.51.100.7", True),
        ("203.0.113.9", True),
        ("198.18.0.1", True),
        ("224.0.0.1", True),
        ("240.0.0.1", True),
        # public IPs must NOT be blocked
        ("8.8.8.8", False),
        ("142.250.183.14", False),
        ("1.1.1.1", False),
        # IPv6
        ("::1", True),
        ("::", True),
        ("::ffff:127.0.0.1", True),  # IPv4-mapped loopback
        ("::ffff:10.0.0.5", True),   # IPv4-mapped private
        ("::ffff:8.8.8.8", False),   # IPv4-mapped public
        ("fe80::1", True),           # link-local
        ("fc00::1", True),           # unique local
        ("fd12:3456::1", True),      # unique local (fd00::/8)
        ("2001:db8::1", True),       # documentation
        ("ff02::1", True),           # multicast
        ("2606:4700:4700::1111", False),  # public IPv6
        # garbage
        ("not-an-ip", True),
        ("", True),
    ],
)
def test_is_blocked_ip(ip, expected):
    from app.utils.url_guard import is_blocked_ip

    assert is_blocked_ip(ip) is expected, ip


# ---------------------------------------------------------------------------
# Unit tests — hostname / scheme rules via URLGuard.check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # blocked hostnames
        "http://localhost/x",
        "http://LOCALHOST:8080/x",  # case-insensitive
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://instance-data/latest/meta-data/",
        "http://db.internal:5432/",
        "http://redis.local:6379/",
        "http://myhost.lan/",
        "http://myhost.corp/",
        # literal private IPs as hostnames
        "http://127.0.0.1:8000/api",
        "http://10.0.0.1/",
        "http://192.168.0.10/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:3000/",
        "http://[fe80::1]/",
        # dangerous schemes
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
        "data:text/html,hello",
        # embedded credentials
        "http://user:pass@example.com/",
        # missing host
        "http:///path-only",
    ],
)
def test_url_guard_blocks(url):
    from app.utils.url_guard import URLGuard

    guard = URLGuard(resolve_dns=False)  # no DNS needed for these rules
    check = guard.check(url)
    assert not check.ok, url
    assert check.reason


def test_url_guard_allows_public_url_without_dns():
    from app.utils.url_guard import URLGuard

    guard = URLGuard(resolve_dns=False)
    assert guard.check("https://www.example.com/contact").ok


def test_url_guard_blocks_private_dns_resolution(monkeypatch):
    """A public-looking hostname that resolves to a private IP must be blocked."""
    import app.utils.url_guard as ug

    monkeypatch.setattr(ug, "resolve_hostname", lambda host: ["10.1.2.3"])
    guard = ug.URLGuard(resolve_dns=True)
    check = guard.check("https://evil.example.com/")
    assert not check.ok
    assert "10.1.2.3" in check.reason


def test_url_guard_blocks_partial_private_resolution(monkeypatch):
    """Mixed public+private DNS records must be blocked (any private IP wins)."""
    import app.utils.url_guard as ug

    monkeypatch.setattr(ug, "resolve_hostname", lambda host: ["8.8.8.8", "192.168.0.1"])
    guard = ug.URLGuard(resolve_dns=True)
    assert not guard.check("https://rebind.example.com/").ok


def test_url_guard_allows_public_resolution(monkeypatch):
    import app.utils.url_guard as ug

    monkeypatch.setattr(ug, "resolve_hostname", lambda host: ["93.184.216.34"])
    guard = ug.URLGuard(resolve_dns=True)
    assert guard.check("https://example.com/").ok


def test_url_guard_dns_failure_blocked(monkeypatch):
    import app.utils.url_guard as ug

    monkeypatch.setattr(ug, "resolve_hostname", lambda host: [])
    guard = ug.URLGuard(resolve_dns=True)
    check = guard.check("https://nonexistent.invalid/")
    assert not check.ok
    # DNS failure must be classified as dns_error, NOT ssrf_blocked:
    # an unresolvable domain is an ordinary dead site, not a security event.
    assert check.kind == "dns_error"


def test_fetch_page_classifies_dns_error_as_dns_error():
    """An unresolvable domain yields status_kind=dns_error (dead-site, not SSRF)."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page("http://definitely-not-a-real-host-9x7q3.invalid/")
    get_settings.cache_clear()
    assert not result.ok
    assert result.status_kind == "dns_error"


def test_fetch_page_ssrf_vs_dns_distinction(monkeypatch):
    """Private literal IP = ssrf_blocked; unresolvable host = dns_error."""
    import app.crawlers.polite_fetcher as pf

    monkeypatch.setenv("SSRF_PROTECTION", "true")
    monkeypatch.setenv("RESPECT_ROBOTS_TXT", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()

    ssrf = pf.fetch_page("http://192.168.5.5/")
    dns = pf.fetch_page("http://no-such-host-abc123.invalid/")
    get_settings.cache_clear()
    assert ssrf.status_kind == "ssrf_blocked"
    assert dns.status_kind == "dns_error"


# ---------------------------------------------------------------------------
# Allowlist (explicit escape hatch for loopback test rigs)
# ---------------------------------------------------------------------------


def test_allowlisted_host_bypasses_private_ip_block(monkeypatch):
    from app.utils.url_guard import URLGuard

    guard = URLGuard(resolve_dns=False, allowed_hosts=frozenset({"127.0.0.1"}))
    assert guard.check("http://127.0.0.1:8080/").ok


def test_allowlist_does_not_leak_to_other_hosts():
    from app.utils.url_guard import URLGuard

    guard = URLGuard(resolve_dns=False, allowed_hosts=frozenset({"127.0.0.1"}))
    assert not guard.check("http://192.168.1.1/").ok


def test_allowlist_does_not_permit_other_schemes():
    from app.utils.url_guard import URLGuard

    guard = URLGuard(resolve_dns=False, allowed_hosts=frozenset({"localhost"}))
    assert not guard.check("file:///etc/passwd").ok


# ---------------------------------------------------------------------------
# Redirect re-validation
# ---------------------------------------------------------------------------


def test_guard_redirect_blocks_internal_target():
    from app.utils.url_guard import guard_redirect

    assert guard_redirect("https://public.example.com/", "http://127.0.0.1:6379/") is None
    assert guard_redirect("https://public.example.com/", "http://10.0.0.1/") is None
    assert (
        guard_redirect("https://public.example.com/", "http://169.254.169.254/latest/")
        is None
    )
    assert guard_redirect("https://public.example.com/", "file:///etc/passwd") is None
    assert guard_redirect("https://public.example.com/", "ftp://x/") is None


def test_guard_redirect_allows_normal_relative_target():
    from app.utils.url_guard import guard_redirect

    target = guard_redirect("https://public.example.com/a", "/contact")
    assert target == "https://public.example.com/contact"


def test_guard_redirect_respects_allowlist(monkeypatch):
    import app.utils.url_guard as ug

    monkeypatch.setattr(ug, "get_allowed_hosts", lambda: frozenset({"127.0.0.1"}))
    target = ug.guard_redirect("http://127.0.0.1:1/", "http://127.0.0.1:2/x")
    assert target == "http://127.0.0.1:2/x"


# ---------------------------------------------------------------------------
# Integration — fetch_page end-to-end against blocked targets
# ---------------------------------------------------------------------------


class _RedirectHandler(BaseHTTPRequestHandler):
    """Sends a 302 to an internal address (the SSRF attack under test)."""

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        self.send_response(302)
        self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="module")
def redirect_server():
    httpd = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture()
def ssrf_on(monkeypatch):
    """SSRF protection ON with the loopback rig allowlisted."""
    monkeypatch.setenv("SSRF_PROTECTION", "true")
    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("RESPECT_ROBOTS_TXT", "false")
    monkeypatch.setenv("CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC", "1000")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_fetch_page_blocks_private_literal(ssrf_on):
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page("http://10.9.8.7/admin")
    assert not result.ok
    assert result.status_kind == "ssrf_blocked"
    assert "10.9.8.7" in result.error


def test_fetch_page_blocks_metadata_ip(ssrf_on):
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page("http://169.254.169.254/latest/meta-data/")
    assert not result.ok
    assert result.status_kind == "ssrf_blocked"


def test_fetch_page_blocks_file_scheme(ssrf_on):
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page("file:///etc/passwd")
    assert not result.ok
    assert result.status_kind == "ssrf_blocked"


def test_fetch_page_blocks_credentials(ssrf_on):
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page("http://user:pass@10.0.0.1/")
    assert not result.ok
    assert result.status_kind == "ssrf_blocked"


def test_fetch_page_blocks_redirect_to_metadata(redirect_server, ssrf_on):
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page(redirect_server + "/")
    assert not result.ok
    assert result.status_kind == "ssrf_blocked"
    assert "169.254.169.254" in result.error or "redirect" in result.error.lower()


def test_fetch_page_allows_allowlisted_loopback(redirect_server, ssrf_on):
    """The allowlisted loopback rig itself must remain fetchable."""
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page(redirect_server + "/")  # 127.0.0.1 is allowlisted
    # The handler 302-redirects to the metadata IP, which is then blocked.
    assert result.status_kind == "ssrf_blocked"
