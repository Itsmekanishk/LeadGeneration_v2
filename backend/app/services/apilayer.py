# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""APILayer suite integration: shared key, graceful degradation.

One APILayer account provides one API key usable across products:

* **mailboxlayer** (``GET https://apilayer.net/api/validate``)
  — email validation with format, MX, SMTP and disposable verdicts.
  Used to enrich website-extracted contacts beyond the keyless Disify
  check: adds ``did_you_mean`` typo suggestions, MX deliverability and
  free-provider detection.

Design mirrors ``_disify_disposable`` in ``contact_normalizer``:

* never raises — any network/HTTP error degrades to ``None`` (unknown)
* process-wide cache keyed by email
* disabled entirely when no key is configured
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings
from app.core.logging_config import event_logger

log = event_logger("locallead.services.apilayer")

_CACHE: dict[str, Optional["MailboxVerdict"]] = {}


@dataclass
class MailboxVerdict:
    """Mailboxlayer verdict for one email address."""

    email: str
    format_valid: Optional[bool] = None
    mx_found: Optional[bool] = None
    smtp_check: Optional[bool] = None
    disposable: Optional[bool] = None
    free: Optional[bool] = None       # gmail/yahoo/... free provider
    role_address: Optional[bool] = None  # info@, support@, ...
    score: Optional[float] = None     # mailboxlayer 0..1 quality score
    did_you_mean: str = ""            # typo suggestion, if any
    source: str = "mailboxlayer"


def mailboxlayer_configured() -> bool:
    """True when an APILayer key is available."""
    return bool(get_settings().apilayer_api_key)


def validate_email(email: str) -> Optional[MailboxVerdict]:
    """Query mailboxlayer for *email* (cached; None = unknown/disabled).

    Never raises: on any failure the verdict is ``None`` and callers keep
    their existing (Disify/local-heuristic) verdicts untouched.
    """
    if not email or "@" not in email:
        return None
    cached = _CACHE.get(email)
    if cached is not None or email in _CACHE:
        return cached

    key = get_settings().apilayer_api_key
    if not key:
        return None

    verdict: Optional[MailboxVerdict] = None
    try:
        import httpx

        base = get_settings().apilayer_mailbox_base_url.rstrip("/")
        url = base if base.endswith(("/check", "/validate")) else f"{base}/validate"
        resp = httpx.get(
            url,
            params={"access_key": key, "email": email, "smtp": "1"},
            timeout=8.0,
        )
        log.info("apilayer_mailboxlayer_call", email=email,
                 status=resp.status_code)
        if resp.status_code == 200:
            payload = resp.json()
            # mailboxlayer error shape: {"error": {"code": ..., "info": ...}}
            if isinstance(payload, dict) and "error" not in payload:
                verdict = MailboxVerdict(
                    email=email,
                    format_valid=payload.get("format_valid"),
                    mx_found=payload.get("mx_found"),
                    smtp_check=payload.get("smtp_check"),
                    disposable=payload.get("disposable"),
                    free=payload.get("free"),
                    role_address=payload.get("role"),
                    score=payload.get("score"),
                    did_you_mean=str(payload.get("did_you_mean") or ""),
                )
            else:
                err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
                log.warning("apilayer_mailboxlayer_error",
                            email=email, code=err.get("code"),
                            info=str(err.get("info"))[:200])
    except Exception:  # noqa: BLE001 — enrichment must never fail crawling
        verdict = None

    _CACHE[email] = verdict
    return verdict
