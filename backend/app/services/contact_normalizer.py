"""Contact extraction & normalization: emails and phone numbers.

Emails:
- Extracted from mailto: links and visible text only — never synthesized.
- Syntax-validated, lowercased, de-duplicated.
- Confidence: HIGH = official business domain, MEDIUM = official page but
  third-party mail host, LOW = found on a different domain than the business.

Phones:
- Provider data is primary; website numbers enrich only.
- Indian numbers normalized to E.164 (+91XXXXXXXXXX); other countries pass
  through best-effort with the raw value preserved.
"""
from __future__ import annotations

from typing import Optional

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

from app.utils.normalization import domain_of, is_aggregator_host

# --------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}"
)
# heuristics to drop false positives commonly found in page source
_JUNK_EMAIL_HINTS = (
    "example.com", "example.org", "sentry.io", "wixpress.com",
    "@2x.png", ".jpg", ".png", ".webp", ".gif", ".svg",
)
_FREE_MAIL_HOSTS = {
    "gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "hotmail.com",
    "outlook.com", "live.com", "rediffmail.com", "icloud.com", "aol.com",
    "protonmail.com", "mail.com", "yandex.com",
}

# Local-parts that never reach a human. Presenting these as leads produces
# guaranteed bounces, so they are dropped at extraction time.
_UNUSABLE_LOCALPARTS = {
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "do_not_reply", "mailer-daemon", "mailerdaemon", "postmaster", "abuse",
    "bounce", "bounces", "auto-reply", "autoreply", "automaticreply",
    "returnmail", "unsubscribe",
}

# Template/placeholder addresses that are never a real mailbox.
_PLACEHOLDER_LOCALPARTS = {
    "youremail", "yourmail", "your-email", "your-email-address", "youremailaddress",
    "name", "yourname", "emailaddress", "email", "test", "sample",
}

# Role mailboxes a business actually monitors — much better outreach targets
# than a personal/first-name address, so they win ties when picking the
# primary email for a lead.
ROLE_MAILBOXES = frozenset({
    "info", "contact", "contactus", "sales", "enquiry", "enquiries", "inquiry",
    "inquiries", "office", "admin", "hello", "support", "care", "help", "team",
    "marketing", "business", "reachus", "connect", "getintouch", "mail", "crm",
    "appointments", "frontdesk", "reception",
})

# obfuscation repairs: \"info [at] hospital dot com\" -> info@hospital.com
_OBFUSCATED = [
    (re.compile(r"\s*\[\s*at\s*\]\s*", re.I), "@"),
    (re.compile(r"\s*\(\s*at\s*\)\s*", re.I), "@"),
    (re.compile(r"\s+at\s+", re.I), "@"),
    (re.compile(r"\s*\[\s*dot\s*\]\s*", re.I), "."),
    (re.compile(r"\s*\(\s*dot\s*\)\s*", re.I), "."),
    (re.compile(r"\s+dot\s+", re.I), "."),
]


@dataclass
class ExtractedEmail:
    """A single email finding with provenance."""

    email: str
    source_url: str
    confidence: str  # HIGH | MEDIUM | LOW
    context: str = ""
    # Disify verdict (True = disposable/temp host, False = not disposable,
    # None = unknown / check disabled).
    disposable: Optional[bool] = None


def _deobfuscate(text: str) -> str:
    for pattern, replacement in _OBFUSCATED:
        text = pattern.sub(replacement, text)
    return text


def is_role_mailbox(email: str) -> bool:
    """True when *email* uses a monitored business role local-part."""
    return email.partition("@")[0].lower() in ROLE_MAILBOXES


def email_priority(email: str) -> int:
    """Rank an email for outreach quality (lower is better).

    Role mailboxes beat role-*prefixed* addresses (``info.pune@``) which beat
    everything else, so the primary email shown for a lead is the one most
    likely to be read."""
    local = email.partition("@")[0].lower()
    if local in ROLE_MAILBOXES:
        return 0
    if any(local.startswith(prefix) for prefix in
           ("info", "contact", "sales", "enquiry", "office", "hello", "support")):
        return 1
    return 2


def extract_emails_from_text(text: str, source_url: str = "",
                             business_domain: str = "",
                             check_disposable: bool = False) -> list[ExtractedEmail]:
    """Extract unique, syntactically-valid emails from raw page text/HTML.

    When *check_disposable* is true, each unique address is also checked
    against Disify (free, keyless API); disposable addresses are downgraded
    to LOW confidence. The verdict is stored on the finding so the pipeline
    can persist it (``BusinessContact.email_disposable``).
    """
    if not text:
        return []
    decoded = html.unescape(text)
    decoded = _deobfuscate(decoded)
    found: dict[str, ExtractedEmail] = {}
    for match in _EMAIL_RE.finditer(decoded):
        email = match.group(0).strip(".,;:<>\"'").lower()
        if not email or "@" not in email:
            continue
        local, _, domain = email.partition("@")
        if not local or not domain or "." not in domain:
            continue
        if any(hint in email for hint in _JUNK_EMAIL_HINTS):
            continue
        if domain.startswith(".") or domain.endswith("."):
            continue
        if local in _UNUSABLE_LOCALPARTS or local in _PLACEHOLDER_LOCALPARTS:
            # never contactable: bounce/abuse endpoints and template addresses
            continue
        if email in found:
            continue
        if business_domain and is_aggregator_host(business_domain):
            # The "business website" is really a third-party directory, review
            # site or social page: any address on it belongs to that platform
            # (e.g. support@practo.com on a Practo listing), never to the
            # business — so it can never be an official-domain (HIGH) contact.
            confidence = "LOW"
        elif business_domain and domain_of(domain) == domain_of(business_domain):
            confidence = "HIGH"
        elif domain in _FREE_MAIL_HOSTS and business_domain:
            confidence = "MEDIUM"
        elif business_domain:
            confidence = "LOW"
        else:
            confidence = "MEDIUM"
        disposable: Optional[bool] = None
        if check_disposable and confidence != "HIGH":
            disposable = _disify_disposable(email)
            if disposable is True:
                # throwaway hosts (mailinator & co) are never a real contact
                confidence = "LOW"
        # APILayer mailboxlayer enrichment (keyless no-op when unset):
        # MX/SMTP-verified verdicts downgrade undeliverable or throwaway
        # addresses; HIGH-confidence emails are only checked when explicitly
        # enabled via MAILBOXLAYER_CHECK_HIGH_CONFIDENCE.
        if check_disposable and (confidence != "HIGH"
                                 or _mailboxlayer_high_conf_enabled()):
            verdict = _mailboxlayer_validate(email)
            if verdict is not None:
                if verdict.disposable:
                    disposable = True
                    confidence = "LOW"
                if verdict.mx_found is False or verdict.smtp_check is False:
                    # mailbox exists in neither DNS nor SMTP — not contactable
                    confidence = "LOW"
        found[email] = ExtractedEmail(
            email=email,
            source_url=source_url,
            confidence=confidence,
            context=decoded[max(0, match.start() - 40): match.end() + 40],
            disposable=disposable,
        )
    return list(found.values())


_DISIFY_CACHE: dict[str, Optional[bool]] = {}


def _mailboxlayer_high_conf_enabled() -> bool:
    """Mailboxlayer also verifies HIGH-confidence emails when enabled."""
    try:
        from app.core.config import get_settings

        return bool(get_settings().mailboxlayer_check_high_confidence)
    except Exception:  # noqa: BLE001 — settings problems disable enrichment
        return False


def _mailboxlayer_validate(email: str):
    """Thin wrapper so tests can patch; returns None when not configured."""
    try:
        from app.services.apilayer import validate_email

        return validate_email(email)
    except Exception:  # noqa: BLE001 — enrichment must never break extraction
        return None


def _disify_disposable(email: str) -> Optional[bool]:
    """Query Disify for a disposable verdict (cached; None = unknown).

    Never raises: on any network/HTTP failure the verdict is None (unknown)
    and the local heuristics keep their original confidence tier.
    """
    if email in _DISIFY_CACHE:
        return _DISIFY_CACHE[email]
    verdict: Optional[bool] = None
    try:
        import httpx

        resp = httpx.get(f"https://disify.com/api/email/{email}",
                         timeout=6.0)
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict) and "disposable" in payload:
                verdict = bool(payload["disposable"])
    except Exception:  # noqa: BLE001 — unreachable service must not fail crawling
        verdict = None
    _DISIFY_CACHE[email] = verdict
    return verdict


def extract_mailto_links(html_text: str, page_url: str,
                         business_domain: str = "",
                         check_disposable: bool = False) -> list[ExtractedEmail]:
    """Pull email addresses from mailto: anchors in HTML."""
    out: list[ExtractedEmail] = []
    for match in re.finditer(
        r"href=[\"']mailto:([^\"'?]+)", html_text or "", re.I
    ):
        raw = unquote(match.group(1)).strip()
        email = raw.split(",")[0].strip().lower()
        email_obj = extract_emails_from_text(email, source_url=page_url,
                                             business_domain=business_domain,
                                             check_disposable=check_disposable)
        out.extend(email_obj)
    return out


def best_email(findings: list[ExtractedEmail]) -> Optional[ExtractedEmail]:
    """Pick the most contactable email: confidence first, then role mailbox."""
    if not findings:
        return None
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(
        findings,
        key=lambda f: (order.get(f.confidence, 9), email_priority(f.email), f.email),
    )[0]


# --------------------------------------------------------------------------
# Phones
# --------------------------------------------------------------------------

# ``tel:`` may be followed directly by the number or by whitespace ("Tel: 020 …").
# The leading \b keeps it from matching the "tel:" inside words like "hotel:".
_TEL_RE = re.compile(r"\btel:\s*\+?\d[\d\s\-\(\)]{6,}\d", re.I)
_WHATSAPP_RE = re.compile(
    r"""(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp://send\?phone=)[/]?(\+?\d{10,15})""",
    re.I,
)
# Optional leading "(" so "(020) 2665 4321" keeps its opening bracket in `raw`
# (the normalized value ignores punctuation either way).
_PHONE_RE = re.compile(r"\(?\+?\d[\d\s\-\(\)\.]{7,16}\d")


def _digits_only(raw: str) -> str:
    return re.sub(r"[^\d]", "", raw)


# A few widely used region aliases that the phonenumbers library spells
# differently from the everyday country code.
_REGION_ALIASES = {"UK": "GB", "EL": "GR", "EN": "GB"}


def _phonenumbers_region(default_country: Optional[str]) -> str:
    """Coerce *default_country* into the UPPERCASE ISO-3166 region code that
    ``phonenumbers.parse`` requires.

    Country codes are stored lowercased throughout the app (e.g. a job's
    ``country="in"``); passing a lowercase region to ``phonenumbers`` raises
    ``NumberParseException``, which previously made every country-less number
    ("09822012345", "9822012345") normalize to an empty string. Fall back to
    India only when the region is missing or unusable.
    """
    region = (default_country or "").strip().upper()
    region = _REGION_ALIASES.get(region, region)
    if len(region) == 2 and region.isalpha():
        return region
    return "IN"


def normalize_phone(raw: Optional[str], default_country: str = "IN") -> tuple[str, str]:
    """Return (normalized_e164_or_empty, country_code).

    Uses the ``phonenumbers`` library when available for correct Indian
    normalization; falls back to a conservative regex-based rule set.
    """
    if not raw:
        return "", ""
    raw = raw.strip()
    try:
        import phonenumbers  # type: ignore

        parsed = phonenumbers.parse(raw, _phonenumbers_region(default_country))
        if phonenumbers.is_valid_number(parsed):
            return (
                f"+{parsed.country_code}{parsed.national_number}",
                f"+{parsed.country_code}",
            )
        return "", ""
    except ImportError:
        pass
    except Exception:
        return "", ""

    # ---- regex fallback (no phonenumbers installed) ----------------------
    digits = _digits_only(raw)
    if not digits:
        return "", ""
    if digits.startswith("00"):
        digits = digits[2:]
    if raw.strip().startswith("+") or digits.startswith("91"):
        if len(digits) == 12 and digits.startswith("91"):
            return f"+{digits}", "+91"
        if len(digits) == 10:
            return f"+91{digits}", "+91"
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}", "+91"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}", "+91"
    return "", ""


@dataclass
class ExtractedPhone:
    """A phone finding with provenance and a priority tier.

    ``confidence`` is HIGH for numbers the site publishes explicitly as
    contact points (structured data, ``tel:`` and WhatsApp links) and MEDIUM
    for numbers merely present in body text.
    """

    raw: str
    source_url: str = ""
    normalized: str = ""
    confidence: str = "MEDIUM"  # HIGH | MEDIUM
    source: str = "text"        # structured | tel | whatsapp | text


def extract_phone_findings(text: str, page_url: str = "",
                           default_country: str = "IN") -> list[ExtractedPhone]:
    """Extract phone numbers with provenance from tel:/WhatsApp links and text.

    Results are de-duplicated on the normalized E.164 value and ordered by
    how explicitly the page published them: WhatsApp/tel links and structured
    data first (HIGH), then body text (MEDIUM).
    """
    if not text:
        return []
    found: list[ExtractedPhone] = []

    def _add(raw: str, source: str, confidence: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        normalized, _cc = normalize_phone(raw, default_country)
        if not normalized:
            return
        found.append(ExtractedPhone(
            raw=raw, source_url=page_url, normalized=normalized,
            confidence=confidence, source=source,
        ))

    # 1. WhatsApp click-to-chat links (an explicit, deliberate contact point)
    for m in _WHATSAPP_RE.finditer(text):
        _add(m.group(1), "whatsapp", "HIGH")
    # 2. tel: links
    for m in _TEL_RE.finditer(text):
        _add(m.group(0)[4:], "tel", "HIGH")
    # 3. Visible text patterns — lower priority, may include landlines etc.
    stripped = _TEL_RE.sub(" ", text)
    stripped = _WHATSAPP_RE.sub(" ", stripped)
    for m in _PHONE_RE.finditer(stripped):
        candidate = m.group(0)
        digits = _digits_only(candidate)
        if 8 <= len(digits) <= 15:
            _add(candidate, "text", "MEDIUM")

    # Dedupe on the normalized value, keeping the first (highest-priority) hit.
    seen: set[str] = set()
    unique: list[ExtractedPhone] = []
    for finding in found:
        if finding.normalized in seen:
            continue
        seen.add(finding.normalized)
        unique.append(finding)
    return unique


def extract_phones_from_text(text: str, page_url: str = "",
                             default_country: str = "IN") -> list[str]:
    """Backwards-compatible wrapper returning raw phone strings."""
    return [f.raw for f in extract_phone_findings(text, page_url, default_country)]
