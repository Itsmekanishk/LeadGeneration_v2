"""Reusable normalization helpers shared by pipeline stages."""
from __future__ import annotations

from typing import Optional

import re
from urllib.parse import urlparse, urlunparse

_NON_WORD = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

# Third-party directories, review sites, marketplaces and social platforms.
# Contact details found on these hosts belong to the platform itself (e.g.
# support@practo.com on a Practo listing), never to the listed business, so
# they must not be treated as the business's own domain/website.
AGGREGATOR_HOSTS: frozenset[str] = frozenset({
    # Global directories / review sites
    "yelp.com", "yellowpages.com", "yellowpages.in", "tripadvisor.com",
    "tripadvisor.in", "foursquare.com", "mapquest.com", "nicelocal.in",
    "cylex.in", "restaurantguru.com", "mouthshut.com", "here.com",
    "trustpilot.com", "glassdoor.com",
    # India: maps are excluded for confidence but included for directory skipping
    "justdial.com", "sulekha.com", "indiamart.com", "practo.com",
    "lybrate.com", "hexahealth.com", "credihealth.com", "medifee.com",
    "medindia.net", "1mg.com", "apollo247.com", "bajajfinservhealth.in",
    "zomato.com", "swiggy.com", "magicpin.in", "eazydiner.com",
    "dineout.co.in", "dineout.com", "nearbuy.com", "slurrp.com",
    "whatshot.in", "lbb.in", "district.in", "gullydirectory.com",
    "explorepune.in", "promallu.com", "yappe.in", "mykharadi.com",
    # Reference / knowledge bases
    "wikipedia.org", "wikidata.org",
    # Social platforms
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "youtube.com", "pinterest.com", "tiktok.com",
})

# Search engines: never a business's official website.
SEARCH_ENGINE_HOSTS: frozenset[str] = frozenset({
    "google.com", "goo.gl", "bing.com", "duckduckgo.com", "yahoo.com",
    "yandex.com", "baidu.com",
})

# Common legal suffixes stripped when comparing business names.
_NAME_SUFFIXES = (
    " pvt ltd", " private limited", " llc", " llp", " inc", " ltd",
    " co", " company", " corporation", " corp",
)


def normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip punctuation/suffixes/whitespace; used for dedup."""
    if not name:
        return ""
    s = _NON_WORD.sub(" ", name.lower())
    s = _WHITESPACE.sub(" ", s).strip()
    changed = True
    while changed and s:
        changed = False
        for suffix in _NAME_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                changed = True
    return s


def normalize_url(url: Optional[str]) -> str:
    """Return a canonical website URL: https, no www, no fragment, no query,
    no trailing slash. Empty string when unusable."""
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if not host or "." not in host:
        return ""
    return urlunparse(("https", host, path, "", "", ""))


def domain_of(url_or_host: Optional[str]) -> str:
    """Registrable-ish domain ('hospital.example.in' stays as-is; scheme/www stripped)."""
    if not url_or_host:
        return ""
    s = url_or_host.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc
    s = s.removeprefix("www.").split("/")[0].split(":")[0]
    return s


def normalize_address(address: Optional[str]) -> str:
    """Squash whitespace/punctuation for dedup comparisons."""
    if not address:
        return ""
    s = _NON_WORD.sub(" ", address.lower())
    return _WHITESPACE.sub(" ", s).strip()


def host_matches(host: Optional[str], hosts: frozenset[str]) -> bool:
    """True when *host* is, or is a subdomain of, any domain in *hosts*.

    Uses host-boundary matching (never substring): ``matrix.com`` must not
    match ``x.com``, whereas ``m.x.com`` and ``x.com`` do.
    """
    h = domain_of(host)
    if not h:
        return False
    return any(h == candidate or h.endswith("." + candidate) for candidate in hosts)


def is_aggregator_host(host: Optional[str]) -> bool:
    """True when *host* is a known directory/review/marketplace/social host."""
    return host_matches(host, AGGREGATOR_HOSTS)


def clean_category(category: Optional[str]) -> str:
    """Humanize a category label for display.

    Providers return either plain labels ("Hospitals") or hierarchical
    machine taxonomies (Geoapify's ``"healthcare, healthcare.hospital"``).
    For the latter we keep only the most specific leaf and humanize it, so the
    UI never shows raw slugs like ``Healthcare.Clinic_Or_Praxis.Paediatrics``.
    """
    if not category:
        return ""
    raw = re.sub(r"\s+", " ", category.strip())
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return ""
    # Most specific = deepest taxonomy path; ties keep the first occurrence.
    best = max(parts, key=lambda p: (p.count("."), len(p)))
    leaf = best.rsplit(".", 1)[-1] if "." in best else best
    leaf = re.sub(r"[_\-]+", " ", leaf).strip()
    return re.sub(r"\s+", " ", leaf).title()


def truncate(s: Optional[str], max_len: int) -> str:
    """Safe truncate for display/storage."""
    if not s:
        return ""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"
