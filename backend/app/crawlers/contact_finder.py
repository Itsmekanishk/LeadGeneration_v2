"""Contact discovery crawler for a business's own public website.

Strategy (bounded and polite):
1. Fetch homepage (robots.txt permitting).
2. From homepage links, pick candidate contact/about pages
   (Contact Us, About, footer links, mailto).
3. Fetch up to ``MAX_PAGES_PER_DOMAIN`` pages total, extracting emails
   (mailto + visible text) and phone numbers (tel: + text patterns).

Every page attempt is recorded in ``crawl_pages``; the crawler never raises
for per-page failures — a dead website cannot fail a search job.
"""
from __future__ import annotations

from typing import Optional

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.crawlers.polite_fetcher import fetch_page, is_allowed_by_robots
from app.services.contact_normalizer import (
    ExtractedEmail,
    ExtractedPhone,
    extract_emails_from_text,
    extract_mailto_links,
    extract_phone_findings,
    normalize_phone,
)
from app.utils.normalization import domain_of

log = event_logger("locallead.crawler.contact")

_CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# href="tel:..." / href="https://wa.me/..." style explicit contact links.
_TEL_HREF_RE = re.compile(
    r"""(?:href=["']tel:|href=["']https?://(?:api\.)?whatsapp\.com/send\?phone="""
    r"""|href=["']https?://wa\.me/)([^"'?&#\s]+)""",
    re.I,
)
_LD_JSON_TYPE = re.compile(r"ld\+json", re.I)
# Longest structured-data phone value still treated as a phone declaration (as
# opposed to a block of page text that merely happens to contain a number).
_MAX_STRUCTURED_PHONE_TEXT = 160
_EMAIL_JSON_KEYS = {"email"}
_PHONE_JSON_KEYS = {"telephone", "phone", "phone_number", "phonenumber"}

_CONTACT_HINTS = (
    "contact", "contact-us", "contactus", "contact_us", "reach-us", "reach_us",
    "get-in-touch", "enquiry", "inquiry", "admission", "admissions", "connect",
    "support", "help", "locate", "location", "address", "branch", "branches"
)
_ABOUT_HINTS = ("about", "about-us", "aboutus", "about_us", "our-story", "team", "overview")
_NAV_SKIP_HOSTS = ("facebook.com", "instagram.com", "twitter.com", "x.com",
                   "linkedin.com", "youtube.com", "pinterest.com", "google.com", "goo.gl")


def _decode_cloudflare_email(cf_hex: str) -> str:
    """Decode Cloudflare email-protection obfuscated email address."""
    try:
        r = int(cf_hex[:2], 16)
        return "".join([chr(int(cf_hex[i:i + 2], 16) ^ r) for i in range(2, len(cf_hex), 2)])
    except Exception:
        return ""


def _loads_json_loose(raw: str):
    """Parse a JSON-LD block, tolerating multi-object / trailing-garbage blocks."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        # Some pages concatenate several JSON objects; decode as many as we can.
        try:
            decoder = json.JSONDecoder()
            values = []
            idx = 0
            while idx < len(raw):
                remainder = raw[idx:].lstrip()
                if not remainder:
                    break
                obj, end = decoder.raw_decode(remainder)
                values.append(obj)
                idx = len(raw) - len(remainder) + end
            return values or None
        except Exception:
            return None


def _walk_json_values(obj, keys: set[str]):
    """Yield every value whose key (case-insensitively) is in *keys*.

    Recurses through dicts and lists, so nested ``contactPoint``/
    ``@graph`` nodes are covered.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in keys and value is not None:
                yield value
            yield from _walk_json_values(value, keys)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_json_values(item, keys)


def _as_strings(value) -> list[str]:
    """Flatten a JSON-LD value (string / list / dict-of-strings) to strings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_as_strings(item))
        return out
    if isinstance(value, dict):
        # Nested contact object, e.g. {"@type": "ContactPoint", "email": "a@b.com"}
        # or the JSON-LD expanded form {"@value": "+91 98765 43210"}.
        out = []
        for k in ("email", "telephone", "phone", "value", "@value"):
            if k in value:
                out.extend(_as_strings(value[k]))
        return out
    return []


def _extract_structured_contact_values(html_text: str) -> tuple[list[str], list[str]]:
    """Read a page's *own* contact declarations: JSON-LD, microdata and meta tags.

    These are the most trustworthy contacts a website can offer (the owner
    publishes them deliberately for machines), so they catch details that a
    plain visible-text scan misses — e.g. an email only present in a schema.org
    ``LocalBusiness`` block or a ``<meta property="og:email">`` tag.
    """
    emails: list[str] = []
    phones: list[str] = []
    soup = BeautifulSoup(html_text or "", "lxml")

    # 1. JSON-LD (schema.org LocalBusiness / Organization / ContactPoint)
    for script in soup.find_all("script"):
        if not _LD_JSON_TYPE.search(str(script.get("type") or "")):
            continue
        data = _loads_json_loose(script.string or script.get_text() or "")
        if data is None:
            continue
        for value in _walk_json_values(data, _EMAIL_JSON_KEYS):
            emails.extend(_as_strings(value))
        for value in _walk_json_values(data, _PHONE_JSON_KEYS):
            phones.extend(_as_strings(value))

    # 2. Microdata / RDFa attributes
    for el in soup.select('[itemprop="email"], [itemprop="telephone"], [itemprop="phone"]'):
        prop = (el.get("itemprop") or "").lower()
        value = el.get("content") or el.get("href") or el.get_text(" ", strip=True)
        if not value:
            continue
        (emails if prop == "email" else phones).append(str(value))

    # 3. Meta tags
    for key, target in (("og:email", "email"), ("og:phone_number", "phone"),
                        ("twitter:email", "email"), ("email", "email"),
                        ("telephone", "phone")):
        tag = (soup.find("meta", attrs={"property": key})
               or soup.find("meta", attrs={"name": key}))
        content = tag.get("content") if tag else None
        if content:
            (emails if target == "email" else phones).append(str(content))

    return emails, phones


def _emails_from_values(values: list[str], *, page_url: str, business_domain: str,
                        check_disposable: bool) -> list[ExtractedEmail]:
    """Validate/score structured-data email values through the normal path."""
    out: list[ExtractedEmail] = []
    for value in values:
        cleaned = re.sub(r"^mailto:", "", str(value).strip(), flags=re.I)
        if not cleaned:
            continue
        out.extend(extract_emails_from_text(
            cleaned, source_url=page_url, business_domain=business_domain,
            check_disposable=check_disposable,
        ))
    return out


def _structured_phone_findings(values: list[str], page_url: str,
                               default_country: str) -> list[ExtractedPhone]:
    """Turn structured-data phone values into HIGH-confidence findings."""
    out: list[ExtractedPhone] = []
    seen: set[str] = set()
    for value in values:
        raw = re.sub(r"^tel:", "", str(value).strip(), flags=re.I)
        if not raw:
            continue
        normalized, _cc = normalize_phone(raw, default_country)
        if not normalized:
            # A structured value may be a short phrase ("Call us on +91 …");
            # scan it for a number. A long value is page text rather than a
            # deliberate contact declaration, so it is never promoted to HIGH
            # confidence — that would launder unrelated numbers from a blob.
            if len(raw) > _MAX_STRUCTURED_PHONE_TEXT:
                continue
            hits = extract_phone_findings(raw, page_url, default_country)
            if not hits:
                continue
            normalized = hits[0].normalized
            raw = hits[0].raw
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(ExtractedPhone(
            raw=raw, source_url=page_url, normalized=normalized,
            confidence="HIGH", source="structured",
        ))
    return out


@dataclass
class PageOutcome:
    """Record of a single crawled page (persisted to crawl_pages)."""

    url: str
    page_type: str
    status: str
    http_status: Optional[int] = None
    emails_found: int = 0
    phones_found: int = 0
    duration_ms: int = 0


@dataclass
class CrawlOutcome:
    """Aggregated result of crawling one business website."""

    start_url: str
    pages: list[PageOutcome] = field(default_factory=list)
    emails: list[ExtractedEmail] = field(default_factory=list)
    # Best-first phone findings with provenance and a confidence tier.
    phone_findings: list[ExtractedPhone] = field(default_factory=list)
    ok: bool = False
    summary: str = ""

    @property
    def phones(self) -> list[str]:
        """Raw phone strings, best-first (backwards-compatible accessor)."""
        return [f.raw for f in self.phone_findings]


def _same_site(base_url: str, link: str) -> bool:
    """True when *link* stays on the business's registrable domain."""
    base_domain = domain_of(urlparse(base_url).netloc)
    link_domain = domain_of(urlparse(link).netloc)
    return bool(link_domain) and link_domain == base_domain


def _extract_links(html_text: str, page_url: str) -> list[tuple[str, str]]:
    """Return (absolute_url, kind) links from HTML; kind in contact|about|other."""
    soup = BeautifulSoup(html_text or "", "lxml")
    out: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        text = anchor.get_text(" ", strip=True).lower()
        joined = urljoin(page_url, href)
        if joined.startswith("mailto:") or joined.startswith("tel:"):
            continue
        parsed = urlparse(joined)
        if parsed.scheme not in ("http", "https"):
            continue
        host = parsed.netloc.lower()
        if any(host.endswith(bad) or host == bad for bad in _NAV_SKIP_HOSTS):
            continue
        kind = "other"
        path = (parsed.path or "").lower()
        if any(h in path or h in text for h in _CONTACT_HINTS):
            kind = "contact"
        elif any(h in path or h in text for h in _ABOUT_HINTS):
            kind = "about"
        out.append((joined, kind))
    return out


def _visible_text(html_text: str) -> str:
    """Render HTML to text, dropping script/style content."""
    soup = BeautifulSoup(html_text or "", "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def crawl_business_website(start_url: str, business_domain: str = "",
                           max_pages: Optional[int] = None,
                           default_country: str = "IN") -> CrawlOutcome:
    """Crawl a business website looking for public contact information.

    *default_country* is the region used to normalize country-less phone
    numbers (e.g. a job's country), so a UK business's "020 7946 0018" is not
    silently rejected by Indian phone rules.

    Never raises for network-level problems; all outcomes are captured in the
    returned CrawlOutcome (see also crawl_pages persistence in the pipeline).
    """
    settings = get_settings()
    max_pages = max_pages or settings.max_pages_per_domain
    outcome = CrawlOutcome(start_url=start_url)
    if not start_url:
        outcome.summary = "no website"
        return outcome

    domain = domain_of(urlparse(start_url).netloc) or business_domain
    if not domain:
        outcome.summary = "unparseable url"
        return outcome

    seen: set[str] = {start_url.rstrip("/")}
    queue: list[tuple[str, str]] = [(start_url, "home")]
    fetched = 0

    while queue and fetched < max_pages:
        url, page_type = queue.pop(0)
        if not is_allowed_by_robots(url):
            outcome.pages.append(PageOutcome(
                url=url, page_type=page_type, status="robots_disallowed",
            ))
            continue
        result = fetch_page(url)
        fetched += 1
        page_outcome = PageOutcome(
            url=result.final_url or url,
            page_type=page_type,
            status=result.status_kind,
            http_status=result.status,
            duration_ms=result.duration_ms,
        )
        if not result.ok:
            outcome.pages.append(page_outcome)
            continue

        outcome.ok = True
        current_page_url = result.final_url or url
        html_text = result.html
        text = _visible_text(html_text)

        # The page's own machine-readable contact declarations (best source).
        structured_emails, structured_phones = _extract_structured_contact_values(html_text)

        emails = extract_mailto_links(html_text, page_url=current_page_url,
                                      business_domain=domain,
                                      check_disposable=settings.enable_disify_checks)
        emails += extract_emails_from_text(text, source_url=current_page_url,
                                           business_domain=domain,
                                           check_disposable=settings.enable_disify_checks)
        emails += _emails_from_values(structured_emails, page_url=current_page_url,
                                      business_domain=domain,
                                      check_disposable=settings.enable_disify_checks)
        # Check for Cloudflare email obfuscation in HTML
        for cf_hex in re.findall(r"""(?:data-cfemail=["']|/cdn-cgi/l/email-protection#)([0-9a-fA-F]+)""", html_text):
            decoded_cf = _decode_cloudflare_email(cf_hex)
            if decoded_cf:
                emails += extract_emails_from_text(decoded_cf, source_url=current_page_url,
                                                   business_domain=domain,
                                                   check_disposable=settings.enable_disify_checks)

        # dedupe by email, keeping the highest-confidence occurrence
        best: dict[str, ExtractedEmail] = {}
        for e in emails:
            current = best.get(e.email)
            if current is None or _CONFIDENCE_ORDER.get(e.confidence, 9) < \
                    _CONFIDENCE_ORDER.get(current.confidence, 9):
                best[e.email] = e
        for e in best.values():
            if e.email not in {x.email for x in outcome.emails}:
                outcome.emails.append(e)
        page_outcome.emails_found = len(best)

        # --- phones: best-first (structured > tel:/WhatsApp links > body text)
        page_phones: list[ExtractedPhone] = _structured_phone_findings(
            structured_phones, current_page_url, default_country)
        for m in _TEL_HREF_RE.finditer(html_text):
            raw = m.group(1).strip()
            normalized, _cc = normalize_phone(raw, default_country)
            if normalized:
                page_phones.append(ExtractedPhone(
                    raw=raw, source_url=current_page_url, normalized=normalized,
                    confidence="HIGH", source="tel",
                ))
        page_phones += extract_phone_findings(text, current_page_url, default_country)

        # keep the first (highest-priority) occurrence of each distinct number
        known = {f.normalized for f in outcome.phone_findings}
        added = 0
        for finding in page_phones:
            if finding.normalized in known:
                continue
            known.add(finding.normalized)
            outcome.phone_findings.append(finding)
            added += 1
        page_outcome.phones_found = added

        outcome.pages.append(page_outcome)

        if fetched < max_pages:
            for link, kind in _extract_links(html_text, current_page_url):
                normalized = link.rstrip("/")
                if normalized in seen or not (_same_site(start_url, link) or _same_site(current_page_url, link)):
                    continue
                if kind == "other":
                    continue
                seen.add(normalized)
                queue.append((normalized, kind))
                if len(queue) >= max_pages * 2:
                    break

    outcome.emails.sort(key=lambda e: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(e.confidence, 9))
    ok_pages = [p for p in outcome.pages if p.status == "ok"]
    outcome.summary = (
        f"{len(ok_pages)}/{len(outcome.pages)} pages ok, "
        f"{len(outcome.emails)} emails, {len(outcome.phones)} phones"
    )
    log.info(
        "website_scan_completed",
        domain=domain,
        pages=len(outcome.pages),
        emails_found=len(outcome.emails),
        phones_found=len(outcome.phones),
    )
    return outcome
