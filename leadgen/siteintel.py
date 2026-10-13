# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""SiteIntel client (Apify actor): metadata, emails and social links.

The master prompt's "SiteIntel" is the Apify-hosted actor
``delightful_unicorn/siteintel-domain-api``. Because it is an Apify actor it
is driven through the Apify API with ``APIFY_TOKEN`` (runs are billed to the
token owner; the actor itself is public).

Two paths are implemented:

* **Actor run** (``APIFY_TOKEN`` set): POST a run, poll until ``SUCCEEDED``,
  then pull dataset items — the master prompt's "extract raw metadata, emails
  and social links".
* **Fallback** (no token): fetch the homepage directly (through the same
  audit/robots/SSRF layer as everything else) and extract title, meta
  description, emails and social links with the standard library. This keeps
  the engine fully functional at zero keys.

Every attempt — actor call, poll or fallback fetch — is returned with an
``audit`` record for the Crawl Audit Trail.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from leadgen import audit
from leadgen.config import get_settings

_APIFY_BASE = "https://api.apify.com/v2"

_SOCIAL_HINTS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "wa.me", "whatsapp.com", "t.me",
    "pinterest.com", "tiktok.com",
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
_JUNK = ("example.com", "example.org", "sentry.io", ".png", ".jpg", ".gif",
         ".svg", ".webp")


@dataclass
class SiteIntelReport:
    """Raw metadata + contacts extracted for one business website."""

    website: str
    domain: str = ""
    title: str = ""
    description: str = ""
    emails: list[str] = field(default_factory=list)
    social_links: list[str] = field(default_factory=list)
    tech: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    source: str = "actor"  # actor | fallback
    audit: list[dict[str, Any]] = field(default_factory=list)


def _domain_of(url: str) -> str:
    host = (urlparse(url).netloc or url).lower()
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _emails_from_text(text: str) -> list[str]:
    found: list[str] = []
    for m in _EMAIL_RE.finditer(text or ""):
        email = m.group(0).strip(".,;:<>\"'").lower()
        if email and not any(j in email for j in _JUNK) and email not in found:
            found.append(email)
    return found


def _socials_from_html(html: str) -> list[str]:
    links: list[str] = []
    for m in re.finditer(r'href=["\'](https?://[^"\']+)', html or "", re.I):
        url = m.group(1)
        host = urlparse(url).netloc.lower()
        if any(host == h or host.endswith("." + h) for h in _SOCIAL_HINTS) \
                and url not in links:
            links.append(url)
    return links


# --------------------------------------------------------------------------
# Apify actor path
# --------------------------------------------------------------------------

def _run_actor_sync(website: str) -> tuple[dict[str, Any] | None, list[dict]]:
    """Run the SiteIntel actor and return (first_dataset_item, audit_rows)."""
    s = get_settings()
    audits: list[dict] = []
    if not s.apify_token:
        return None, audits

    run_url = f"{_APIFY_BASE}/acts/{s.siteintel_actor}/run-sync-get-dataset-items"
    result = audit.http_get(
        run_url,
        params={"token": s.apify_token, "url": website},
        api_exempt=True,
    )
    audits.append(audit.audit_record(result, "siteintel", {"phase": "run"}))
    if not result.ok or result.json is None:
        return None, audits
    items = result.json if isinstance(result.json, list) else []
    if items and isinstance(items[0], dict):
        return items[0], audits
    return None, audits


# --------------------------------------------------------------------------
# Fallback path (no token): direct fetch + stdlib extraction
# --------------------------------------------------------------------------

def _fallback_intel(website: str) -> SiteIntelReport:
    report = SiteIntelReport(website=website, domain=_domain_of(website),
                             source="fallback")
    result = audit.http_get(website, headers={"Accept": "text/html,*/*"})
    report.audit.append(audit.audit_record(result, "siteintel",
                                           {"phase": "homepage"}))
    if not result.ok:
        return report
    html = result.content or ""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if title_m:
        report.title = re.sub(r"\s+", " ", title_m.group(1)).strip()[:300]
    desc_m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html, re.I | re.S)
    if desc_m:
        report.description = desc_m.group(1).strip()[:500]
    report.emails = _emails_from_text(html)
    report.social_links = _socials_from_html(html)
    report.raw = {"http_status": result.status,
                  "response_time_ms": result.response_time_ms}
    return report


def gather_site_intel(website: str) -> SiteIntelReport:
    """Extract metadata, emails and social links for *website*.

    Uses the SiteIntel Apify actor when ``APIFY_TOKEN`` is configured;
    otherwise (or when the actor fails) falls back to a direct, polite,
    robots-respecting homepage fetch.
    """
    if not website:
        report = SiteIntelReport(website="")
        return report
    item, audits = _run_actor_sync(website)
    if item is not None:
        domain = _domain_of(website)
        emails = [
            e.strip().lower()
            for e in (item.get("emails") or []) if isinstance(e, str)
        ] or _emails_from_text(str(item))
        report = SiteIntelReport(
            website=website,
            domain=item.get("domain") or domain,
            title=str(item.get("title") or item.get("siteTitle") or "")[:300],
            description=str(item.get("description") or "")[:500],
            emails=emails,
            social_links=[u for u in (item.get("socialLinks")
                                      or []) if isinstance(u, str)],
            tech=item.get("tech") if isinstance(item.get("tech"), dict) else {},
            raw=item,
            source="actor",
        )
        report.audit = audits
        return report
    report = _fallback_intel(website)
    report.audit = audits + report.audit
    return report


def wait_for_rate_limit() -> None:
    """Politeness pause between businesses (configurable via .env)."""
    delay = get_settings().request_delay_seconds
    if delay > 0:
        time.sleep(delay)
