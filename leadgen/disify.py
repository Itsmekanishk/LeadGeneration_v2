# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Disify client: free disposable-email detection (keyless).

``GET https://disify.com/api/email/{address}`` →
``{"format": true, "domain": "...", "disposable": false, "dns": true}``

``disposable=True`` covers throwaway hosts (mailinator, yopmail, …). Note
that mainstream free providers (gmail.com, outlook.com, …) are *not* flagged
by Disify — the engine treats them via its own known-free-host list so the
MEDIUM confidence tier matches the master prompt's intent. When Disify is
unreachable the result is ``None`` (unknown) — never an exception — and the
local list decides.

Every call is audit-logged (service="disify").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

from leadgen import audit
from leadgen.config import get_settings

# Mainstream free mail hosts — a match means the email is not on the
# business's own domain (relevant to the MEDIUM confidence tier).
FREE_MAIL_HOSTS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "ymail.com", "hotmail.com", "hotmail.co.in", "outlook.com",
    "outlook.in", "live.com", "msn.com", "rediffmail.com", "icloud.com",
    "me.com", "aol.com", "protonmail.com", "proton.me", "mail.com",
    "yandex.com", "yandex.ru", "zoho.com", "gmx.com",
}

# Local safety net when Disify is unreachable.
_KNOWN_DISPOSABLE = {
    "mailinator.com", "yopmail.com", "guerrillamail.com", "10minutemail.com",
    "tempmail.com", "temp-mail.org", "throwawaymail.com", "sharklasers.com",
    "getnada.com", "dispostable.com", "trashmail.com", "maildrop.cc",
}


@dataclass
class DisifyResult:
    """Outcome of one disposable check (None fields ⇒ unknown)."""

    email: str
    disposable: bool | None = None
    is_free_host: bool = False
    format_ok: bool | None = None
    dns_ok: bool | None = None
    source: str = ""   # disify | local | unreachable
    audit: dict = field(default_factory=dict)


def check_email(email: str) -> DisifyResult:
    """Check *email* against Disify (and the local free-host list).

    Never raises: a network/HTTP failure yields ``source="unreachable"`` with
    ``disposable=None`` and a local-list verdict so processing can continue.
    """
    email = (email or "").strip().lower()
    result = DisifyResult(email=email)
    if not email or "@" not in email:
        result.audit = {"service": "disify", "url": "", "http_status": None,
                        "response_time_ms": 0, "robots_allowed": True,
                        "status_kind": "skipped_invalid", "error": ""}
        return result
    domain = email.split("@")[-1]
    result.is_free_host = domain in FREE_MAIL_HOSTS
    if domain in _KNOWN_DISPOSABLE:
        result.disposable, result.source = True, "local"

    s = get_settings()
    api_result = audit.http_get(
        f"{s.disify_base_url}/email/{quote(email)}", api_exempt=True)
    result.audit = audit.audit_record(api_result, "disify")
    if api_result.ok and isinstance(api_result.json, dict):
        payload = api_result.json
        result.format_ok = bool(payload.get("format"))
        result.dns_ok = None if payload.get("dns") is None else bool(
            payload.get("dns"))
        result.disposable = bool(payload.get("disposable"))
        result.source = "disify"
    elif result.source != "local":
        result.source = "unreachable"
        result.audit.setdefault(
            "error", "disify unreachable; local list used")
    return result
