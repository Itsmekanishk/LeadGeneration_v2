# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Automated OSINT website and contact discovery service.

Bridges the gap between open map datasets (OpenStreetMap, Geoapify) and real-world
lead generation:
1. When a business has no website or phone in the place database, performs
   intelligent domain probing (.in, .com, .co.in), OSM extratags queries,
   and web search resolution.
2. Discovers official website URLs, Facebook/Instagram business pages, and
   direct contact phone numbers and emails found in public search knowledge.
3. Feeds discovered websites into the deep contact crawler for WhatsApp & email
   extraction.
"""
from __future__ import annotations

import re
import socket
import ssl
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.services.contact_normalizer import normalize_phone
from app.utils.normalization import (
    AGGREGATOR_HOSTS,
    SEARCH_ENGINE_HOSTS,
    host_matches,
    normalize_url,
)

log = event_logger("locallead.osint.finder")

# Hosts that must never be reported as a business's official website: paid
# directories, review sites, marketplaces, social platforms, search engines
# and reference/knowledge bases. Matched on host boundaries (``x.com`` no
# longer swallows unrelated domains such as ``matrix.com``).
_IGNORED_HOSTS: frozenset[str] = (
    AGGREGATOR_HOSTS
    | SEARCH_ENGINE_HOSTS
    | frozenset({"uservoice.com", "surveymonkey.com", "trustpilot.com"})
)

_SOCIAL_DOMAINS = {"facebook.com", "instagram.com", "linkedin.com"}

_EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
# Indian landline/mobile formats plus any explicit international (+CC…) number;
# every candidate is still validated through ``normalize_phone`` afterwards.
_PHONE_REGEX = re.compile(
    r"(?:\+?91[\s\-]?)?(?:020[\s\-]?)?[6789]\d{9}|020[\s\-]?\d{7,8}"
    r"|\+\d[\d\s\-().]{7,16}\d"
)

# Words too generic to identify a specific business in a search snippet.
_NAME_STOPWORDS = {
    "the", "and", "for", "with", "pvt", "ltd", "limited", "private", "inc",
    "llp", "company", "clinic", "hospital", "school", "college", "center",
    "centre", "shop", "store", "hotel", "restaurant", "cafe", "services",
    "service", "group", "care", "health", "medical", "nursing", "home",
}


def _distinctive_tokens(text: str) -> set[str]:
    """Lowercase words long enough to identify a business, minus stopwords."""
    tokens = re.findall(r"[a-z0-9]{4,}", (text or "").lower())
    return {t for t in tokens if t not in _NAME_STOPWORDS}


def _attributable_snippet_text(snippets: list[str], name: str,
                               location: str) -> str:
    """Join only the snippets that plausibly describe *this* business.

    A results page mixes many businesses, so a phone number found in an
    unrelated snippet is someone else's. We keep snippets that mention a
    distinctive token from the business name (or, failing that, the location)
    and refuse extraction entirely when nothing can be attributed — an empty
    result beats a wrong contact.
    """
    name_tokens = _distinctive_tokens(name)
    location_tokens = _distinctive_tokens(location)
    by_name: list[str] = []
    by_location: list[str] = []
    for snippet in snippets:
        low = snippet.lower()
        if name_tokens and any(tok in low for tok in name_tokens):
            by_name.append(snippet)
        elif location_tokens and any(tok in low for tok in location_tokens):
            by_location.append(snippet)
    if by_name:
        return " ".join(by_name)
    if by_location:
        return " ".join(by_location)
    return ""

_OSINT_CACHE: dict[str, dict[str, Any]] = {}
_MAX_CACHE_SIZE = 5000


def _make_ssl_context() -> ssl.SSLContext:
    """Create SSL context tuned for compatibility across Alpine and server CDNs."""
    ctx = ssl.create_default_context()
    try:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    except Exception:
        pass
    return ctx


@dataclass
class OsintResult:
    """Resolved web presence for a business."""

    website: str = ""
    social_urls: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    source: str = ""


def _clean_slug(name: str) -> list[str]:
    """Generate candidate domain slugs from a business name."""
    clean = re.sub(r"&", " and ", name.lower())
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", clean)
    words = clean.split()
    if not words:
        return []
    # Drop common filler words
    stopwords = {"and", "the", "in", "at", "by", "of", "cafe", "coffee", "restaurant", "hotel", "clinic", "hospital"}
    meaningful = [w for w in words if w not in stopwords] or words

    full_slug = "".join(words)
    meaningful_slug = "".join(meaningful)
    no_and_slug = "".join([w for w in words if w != "and"])
    short_slug = "".join(words[:2]) if len(words) > 2 else full_slug
    first_word_slug = words[0] if len(words[0]) >= 4 else ""

    slugs = []
    for s in [full_slug, meaningful_slug, no_and_slug, short_slug, first_word_slug]:
        if s and len(s) >= 4 and s not in slugs:
            slugs.append(s)
    return slugs


def _probe_domains(name: str, country: str = "in") -> Optional[str]:
    """Fast, zero-cost DNS & HTTP probe for candidate official domains."""
    slugs = _clean_slug(name)
    if not slugs:
        return None

    tlds = [".in", ".co.in", ".com"] if country.lower() == "in" else [".com", f".{country.lower()}", ".org"]
    candidates = []
    for slug in slugs:
        for tld in tlds:
            candidates.append(f"{slug}{tld}")

    for domain in candidates[:8]:
        try:
            # Fast DNS check: throws gaierror if domain does not exist
            socket.getaddrinfo(domain, 80, socket.AF_INET, socket.SOCK_STREAM)
            # Verify HTTP availability
            test_url = f"https://{domain}"
            with httpx.Client(timeout=4.0, follow_redirects=True, verify=_make_ssl_context()) as client:
                resp = client.head(test_url)
                if resp.status_code < 400:
                    return str(resp.url)
        except Exception:
            continue
    return None


def _search_serper(name: str, location: str, api_key: str) -> Optional[OsintResult]:
    """Search Google knowledge graph via Serper.dev if configured."""
    if not api_key:
        return None
    try:
        url = "https://google.serper.dev/places"
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        body = {"q": f"{name} {location}".strip(), "limit": 1}
        with httpx.Client(timeout=5.0, verify=_make_ssl_context()) as client:
            resp = client.post(url, headers=headers, json=body)
            if resp.status_code == 200:
                data = resp.json()
                places = data.get("places", [])
                if places:
                    p = places[0]
                    res = OsintResult(source="serper")
                    if p.get("website"):
                        res.website = normalize_url(p["website"])
                    if p.get("phoneNumber"):
                        res.phones.append(p["phoneNumber"])
                    return res
    except Exception as exc:
        log.warning("serper_osint_error", error=str(exc))
    return None


def _search_nominatim_extratags(name: str, location: str) -> Optional[OsintResult]:
    """Fast zero-cost OSM Nominatim search with extratags contact enrichment."""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        clean_name = re.sub(r"[^a-zA-Z0-9\s]", " ", name)
        clean_loc = re.sub(r"[^a-zA-Z0-9\s]", " ", location)
        query = f"{clean_name} {clean_loc}".strip()
        params = {"q": query, "format": "json", "extratags": 1, "limit": 1}
        headers = {
            "User-Agent": (
                "LocalLeadIntelligence/1.0 (+https://github.com/locallead-intel; contact@localleadintel.org)"
            )
        }
        with httpx.Client(timeout=3.5, verify=_make_ssl_context(), headers=headers) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    ex = data[0].get("extratags") or {}
                    res = OsintResult(source="osm_extratags")
                    web = ex.get("website") or ex.get("contact:website", "")
                    if web:
                        res.website = normalize_url(web)
                    ph = ex.get("phone") or ex.get("contact:phone") or ex.get("contact:mobile", "")
                    if ph:
                        res.phones.append(ph)
                    em = ex.get("email") or ex.get("contact:email", "")
                    if em:
                        res.emails.append(em)
                    if res.website or res.phones or res.emails:
                        return res
    except Exception as exc:
        log.warning("nominatim_extratags_error", biz_name=name, error=str(exc)[:150])
    return None


def _search_web_yahoo(name: str, location: str) -> OsintResult:
    """Query Yahoo Search for official website, social links, and snippets."""
    clean_name = re.sub(r"[^a-zA-Z0-9\s]", " ", name)
    clean_loc = re.sub(r"[^a-zA-Z0-9\s]", " ", location)
    query = re.sub(r"\s+", " ", f"{clean_name} {clean_loc} phone number").strip()
    url = f"https://search.yahoo.com/search?p={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    result = OsintResult(source="yahoo_web")
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True, headers=headers, verify=_make_ssl_context()) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return result

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "lxml")

            for a in soup.find_all("a", href=True):
                raw_href = a["href"]
                m = re.search(r"/RU=([^/]+)/", raw_href)
                u = urllib.parse.unquote(m.group(1)) if m else raw_href
                if not u.startswith("http"):
                    continue
                parsed = urllib.parse.urlparse(u)
                host = parsed.netloc.lower().replace("www.", "")

                if any(soc in host for soc in _SOCIAL_DOMAINS):
                    clean_social = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
                    if clean_social not in result.social_urls and len(result.social_urls) < 3:
                        result.social_urls.append(clean_social)
                elif host and not host_matches(host, _IGNORED_HOSTS):
                    if not result.website:
                        result.website = normalize_url(u)

            # Extract phone & email only from snippets attributable to this
            # business (never from the whole page — that pulled in numbers
            # belonging to unrelated results).
            snippet_texts = [
                div.get_text(" ", strip=True)
                for div in soup.find_all("div", class_="compText")
            ]
            snippets = _attributable_snippet_text(snippet_texts, name, location)
            for m in _PHONE_REGEX.finditer(snippets):
                cand = m.group(0).strip()
                norm, _cc = normalize_phone(cand)
                if norm and norm not in result.phones:
                    result.phones.append(cand)

            for m in _EMAIL_REGEX.finditer(snippets):
                email = m.group(0).strip(".,;:<>\"'").lower()
                email = re.sub(r"^\d{5,6}", "", email)
                if "@" in email and not email.endswith((".png", ".jpg", ".webp", ".svg", "yahoo.com", "yimg.com")):
                    if email not in result.emails:
                        result.emails.append(email)

    except Exception as exc:
        log.warning("yahoo_osint_error", biz_name=name, err_type=type(exc).__name__, error=str(exc)[:200])

    return result


def _search_web_duckduckgo(name: str, location: str) -> OsintResult:
    """Query DuckDuckGo for official website, social links, and snippets."""
    clean_name = re.sub(r"[^a-zA-Z0-9\s]", " ", name)
    clean_loc = re.sub(r"[^a-zA-Z0-9\s]", " ", location)
    query = re.sub(r"\s+", " ", f"{clean_name} {clean_loc} contact").strip()
    url = "https://html.duckduckgo.com/html/"
    data = {"q": query}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    result = OsintResult(source="ddg_web")
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True, headers=headers, verify=_make_ssl_context()) as client:
            resp = client.post(url, data=data)
            if resp.status_code not in (200, 202):
                return result

            html_text = resp.text
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_text, "lxml")

            for a in soup.find_all("a", class_="result__url"):
                raw_href = a.get("href", "")
                m = re.search(r"uddg=([^&]+)", raw_href)
                u = urllib.parse.unquote(m.group(1)) if m else a.get_text(strip=True)
                if not u.startswith("http"):
                    u = f"https://{u}"
                parsed = urllib.parse.urlparse(u)
                host = parsed.netloc.lower().replace("www.", "")

                if any(soc in host for soc in _SOCIAL_DOMAINS):
                    if u not in result.social_urls and len(result.social_urls) < 3:
                        result.social_urls.append(u)
                elif host and not host_matches(host, _IGNORED_HOSTS):
                    if not result.website:
                        result.website = normalize_url(u)

            # Extract phone & email snippets (attributable to this business only)
            snippet_texts = [
                s.get_text(" ", strip=True)
                for s in soup.find_all(class_="result__snippet")
            ]
            snippets = _attributable_snippet_text(snippet_texts, name, location)
            for m in _PHONE_REGEX.finditer(snippets):
                cand = m.group(0).strip()
                norm, _cc = normalize_phone(cand)
                if norm and norm not in result.phones:
                    result.phones.append(cand)

            for m in _EMAIL_REGEX.finditer(snippets):
                email = m.group(0).strip(".,;:<>\"'").lower()
                email = re.sub(r"^\d{5,6}", "", email)
                if "@" in email and not email.endswith((".png", ".jpg", ".webp", ".svg", "duckduckgo.com")):
                    if email not in result.emails:
                        result.emails.append(email)

    except Exception as exc:
        log.warning("ddg_osint_error", biz_name=name, error=str(exc)[:200])

    return result


def resolve_business_web_presence(
    name: str,
    location: str = "",
    country: str = "in",
) -> OsintResult:
    """Find a business's website, social presence, and direct contacts.

    Multi-tiered resolution strategy:
    1. Check memory cache.
    2. Try Serper.dev if API key configured (instant Google knowledge graph).
    3. OSM Nominatim extratags lookup (zero-cost direct tags).
    4. Fast zero-cost domain DNS & HTTP probing (.in, .co.in, .com).
    5. Web intelligence (Yahoo Search) for official URL & search snippets.
    6. DuckDuckGo fallback.
    """
    clean_name = name.strip()
    if not clean_name:
        return OsintResult()

    cache_key = f"{clean_name.lower()}:{location.lower()}:{country.lower()}"
    if cache_key in _OSINT_CACHE:
        cached = _OSINT_CACHE[cache_key]
        return OsintResult(
            website=cached.get("website", ""),
            social_urls=cached.get("social_urls", []),
            phones=cached.get("phones", []),
            emails=cached.get("emails", []),
            source="cache",
        )

    settings = get_settings()
    result = OsintResult()

    # 1. Serper (if key configured)
    if settings.serper_api_key:
        serper_res = _search_serper(clean_name, location, settings.serper_api_key)
        if serper_res:
            if serper_res.website:
                result.website = serper_res.website
            result.phones.extend(serper_res.phones)
            result.source = "serper"

    # 2. OSM Nominatim extratags (fast, free, zero-cost)
    osm_res = _search_nominatim_extratags(clean_name, location)
    if osm_res:
        if osm_res.website:
            result.website = osm_res.website
        for ph in osm_res.phones:
            if ph not in result.phones:
                result.phones.append(ph)
        for em in osm_res.emails:
            if em not in result.emails:
                result.emails.append(em)
        result.source = result.source or "osm_extratags"

    # 3. Fast domain probing (.in, .co.in, .com)
    if not result.website:
        probed_site = _probe_domains(clean_name, country)
        if probed_site:
            result.website = probed_site
            result.source = result.source or "domain_probe"

    # 4. Yahoo Web Search resolution (highest accuracy with location context)
    if not result.website or not result.phones:
        yahoo_res = _search_web_yahoo(clean_name, location)
        if not result.website and yahoo_res.website:
            result.website = yahoo_res.website
        for ph in yahoo_res.phones:
            if ph not in result.phones:
                result.phones.append(ph)
        for em in yahoo_res.emails:
            if em not in result.emails:
                result.emails.append(em)
        for soc in yahoo_res.social_urls:
            if soc not in result.social_urls:
                result.social_urls.append(soc)
        if not result.source and (yahoo_res.website or yahoo_res.phones or yahoo_res.emails):
            result.source = "yahoo_web"

    # 5. DuckDuckGo fallback (if still missing contacts)
    if not result.website and not result.phones:
        web_res = _search_web_duckduckgo(clean_name, location)
        if web_res.website:
            result.website = web_res.website
        for ph in web_res.phones:
            if ph not in result.phones:
                result.phones.append(ph)
        for em in web_res.emails:
            if em not in result.emails:
                result.emails.append(em)
        if not result.source and (web_res.website or web_res.phones):
            result.source = "ddg_web"

    if result.website or result.phones or result.emails:
        _cache_result(cache_key, result)

    return result


def _cache_result(key: str, res: OsintResult) -> None:
    if not (res.website or res.phones or res.emails):
        return  # Do not cache empty/failed lookups
    if len(_OSINT_CACHE) >= _MAX_CACHE_SIZE:
        _OSINT_CACHE.clear()
    _OSINT_CACHE[key] = {
        "website": res.website,
        "social_urls": res.social_urls,
        "phones": res.phones,
        "emails": res.emails,
    }
