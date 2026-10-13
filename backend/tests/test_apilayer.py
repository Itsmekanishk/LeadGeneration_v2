"""Tests for the APILayer unified-suite integration.

All network access is faked; the suite runs fully offline.
Covers: mailboxlayer client, contact_normalizer wiring, leadgen key fallback.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import apilayer  # noqa: E402
from app.services import contact_normalizer as cn  # noqa: E402


# ---------------------------------------------------------------------------
# mailboxlayer client
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _reset(monkeypatch, key="test-key"):
    monkeypatch.setattr(apilayer, "_CACHE", {})
    monkeypatch.setattr(apilayer.get_settings(), "apilayer_api_key", key)
    monkeypatch.setattr(apilayer.get_settings(),
                        "apilayer_mailbox_base_url", "http://apilayer.test/api")


def test_validate_email_parses_verdict(monkeypatch):
    _reset(monkeypatch)
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp({
            "email": "info@apexhospital.in", "format_valid": True,
            "mx_found": True, "smtp_check": True, "disposable": False,
            "free": False, "role": True, "score": 0.86,
            "did_you_mean": "",
        })

    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)
    v = apilayer.validate_email("info@apexhospital.in")
    assert v is not None
    assert v.format_valid is True and v.mx_found is True
    assert v.disposable is False and v.free is False
    assert v.role_address is True and v.score == 0.86
    assert "apilayer.test/api/validate" in captured["url"]
    assert captured["params"]["access_key"] == "test-key"
    # cached on second call (no extra HTTP)
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no 2nd call")))
    assert apilayer.validate_email("info@apexhospital.in") is v


def test_validate_email_disabled_without_key(monkeypatch):
    _reset(monkeypatch, key="")
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP")))
    assert apilayer.validate_email("x@y.com") is None
    assert apilayer.mailboxlayer_configured() is False


def test_validate_email_never_raises(monkeypatch):
    _reset(monkeypatch)
    import httpx

    def boom(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(httpx, "get", boom)
    assert apilayer.validate_email("a@b.com") is None


def test_validate_email_rejects_api_error_payload(monkeypatch):
    _reset(monkeypatch)
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(
        {"error": {"code": 101, "info": "invalid key"}}))
    assert apilayer.validate_email("a@b.com") is None


def test_validate_email_bad_input_short_circuits(monkeypatch):
    _reset(monkeypatch)
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP")))
    assert apilayer.validate_email("not-an-email") is None


# ---------------------------------------------------------------------------
# contact_normalizer wiring
# ---------------------------------------------------------------------------

def _email_verdict(**kw):
    return apilayer.MailboxVerdict(email="t@x.com", **kw)


def test_normalizer_downgrades_no_mx(monkeypatch):
    # third-party host → MEDIUM baseline; MX missing → LOW
    monkeypatch.setattr(cn, "_mailboxlayer_validate",
                        lambda e: _email_verdict(mx_found=False, smtp_check=None))
    findings = cn.extract_emails_from_text(
        "contact sales@partner-portal.co now", "https://realbusiness.in/contact",
        business_domain="realbusiness.in", check_disposable=True)
    assert findings and findings[0].confidence == "LOW"


def test_normalizer_downgrades_disposable_via_mailboxlayer(monkeypatch):
    monkeypatch.setattr(cn, "_mailboxlayer_validate",
                        lambda e: _email_verdict(disposable=True))
    findings = cn.extract_emails_from_text(
        "mail tmp@mailinator.com", "https://some.site/x",
        business_domain="some.site", check_disposable=True)
    assert findings[0].confidence == "LOW"
    assert findings[0].disposable is True


def test_normalizer_keeps_deliverable_high(monkeypatch):
    monkeypatch.setattr(cn, "_mailboxlayer_validate",
                        lambda e: _email_verdict(
                            mx_found=True, smtp_check=True, disposable=False))
    findings = cn.extract_emails_from_text(
        "reach info@realbusiness.in", "https://realbusiness.in/",
        business_domain="realbusiness.in", check_disposable=True)
    assert findings[0].confidence == "HIGH"


def test_normalizer_high_conf_skipped_by_default(monkeypatch):
    # HIGH emails are not verified unless MAILBOXLAYER_CHECK_HIGH_CONFIDENCE
    monkeypatch.setattr(cn, "_mailboxlayer_validate",
                        lambda e: (_ for _ in ()).throw(AssertionError("must not run")))
    findings = cn.extract_emails_from_text(
        "reach info@realbusiness.in", "https://realbusiness.in/",
        business_domain="realbusiness.in", check_disposable=True)
    assert findings[0].confidence == "HIGH"


def test_normalizer_untouched_without_key(monkeypatch):
    # validator returns None (no key configured) → heuristics win unchanged
    monkeypatch.setattr(cn, "_mailboxlayer_validate", lambda e: None)
    findings = cn.extract_emails_from_text(
        "mail someone@gmail.com", "https://realbusiness.in/",
        business_domain="realbusiness.in", check_disposable=True)
    assert findings[0].confidence == "MEDIUM"


# ---------------------------------------------------------------------------
# leadgen config: APILAYER_API_KEY falls back for Numverify
# ---------------------------------------------------------------------------

def test_leadgen_numverify_uses_apilayer_fallback(monkeypatch):
    leadgen_dir = Path(__file__).resolve().parents[2] / "leadgen"
    sys.path.insert(0, str(leadgen_dir.parent))
    for mod in [m for m in list(sys.modules) if m == "leadgen.config"]:
        del sys.modules[mod]
    import importlib

    import leadgen.config as lgcfg
    importlib.reload(lgcfg)
    monkeypatch.setenv("APILAYER_API_KEY", "shared-key-123")
    monkeypatch.delenv("NUMVERIFY_API_KEY", raising=False)
    s = lgcfg.get_settings(refresh=True)
    assert s.numverify_api_key == "shared-key-123"
