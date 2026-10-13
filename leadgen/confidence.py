# leadgen — Open-source OSINT lead generation inputs.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Email Confidence logic (the engine's "brain" for addresses).

Tier rules (master prompt, corrected):

* **HIGH** — the email's domain matches the business website's domain
  (extracted via SiteIntel). Strongest signal of an official contact.
* **MEDIUM** — email was found **on the official business website** and its
  domain is a known free/standard mail host (checked via Disify + local
  free-host list) that is *not* disposable.
* **LOW** — anything else: external pages, third-party directories, or
  disposable hosts (even if found on the official site).

This corrects the master prompt's snippet, whose ``elif is_disposable ==
False → MEDIUM`` made LOW unreachable for non-disposable emails and never
checked where the email was found; the corrected logic requires the
"found on the official website" condition the prompt's own MEDIUM definition
states, and maps disposable hosts to LOW.
"""
from __future__ import annotations

from dataclasses import dataclass

from leadgen.disify import DisifyResult, FREE_MAIL_HOSTS
from leadgen.siteintel import _domain_of


@dataclass
class EmailVerdict:
    """Confidence tier + reasoning for one email address."""

    email: str
    confidence: str            # HIGH | MEDIUM | LOW
    reasons: list[str]


def _host_of(url_or_domain: str) -> str:
    return _domain_of(url_or_domain or "")


def calculate_email_confidence(email: str, website_url: str,
                               is_disposable: bool | None,
                               found_on_website: bool = True,
                               disify: DisifyResult | None = None) -> str:
    """Return the Email Confidence tier for one email.

    ``is_disposable`` is the Disify verdict (``None`` = unknown/unreachable —
    the local list then decides). ``found_on_website`` records whether the
    address was extracted from the business's own site (SiteIntel homepage /
    contact pages) or from external pages/directories.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return "LOW"
    domain = _host_of(website_url)
    email_domain = email.split("@")[-1]

    # HIGH: domain match with the business website (via SiteIntel).
    if domain and email_domain == domain:
        return "HIGH"
    if domain and (email_domain == f"mail.{domain}" or
                   email_domain.endswith(f".{domain}")):
        return "HIGH"

    # MEDIUM: free/standard host (Disify: disposable == False), but only when
    # the address was actually found on the official business website.
    if found_on_website and is_disposable is False and \
            email_domain in FREE_MAIL_HOSTS:
        return "MEDIUM"

    # LOW: external pages, third-party directories, disposable or unknown.
    return "LOW"
