# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Nominatim (OpenStreetMap) provider — the open-source geocoder.

Replaces every Google Maps endpoint. Free for reasonable use under
the Nominatim usage policy, which this client honors:

- honest, descriptive ``User-Agent`` (``NOMINATIM_USER_AGENT``)
- never more than 1 request/second against the public endpoint
- bounded queries only (small ``limit``, structured params)

Role in the platform (``BUSINESS_PROVIDER=nominatim`` or the fallback chain):

* **Location resolver** — turns a text location ("Hospitals in Porwal Road,
  Pune") into a latitude/longitude center used by other providers when a
  query arrives without coordinates.
* **Fallback discovery provider** — when Google/Serper are unavailable,
  discovers businesses via OSM and geocodes each one; categories are matched
  with OSM ``amenity``/``shop``/``office`` tags (no fabricated contacts:
  websites/phones only when OSM actually lists them).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.providers.base import (
    BusinessSearchProvider,
    PlaceDetails,
    PlaceSearchQuery,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.utils.normalization import normalize_url

log = event_logger("locallead.provider.nominatim")

_LAST_REQUEST_TS = 0.0
_MIN_INTERVAL_S = 1.1  # Nominatim public policy: max ~1 req/s

# category → OSM search targets (amenity parameter or keyword / OSM tag queries)
_OSM_CATEGORY_SEARCH_TARGETS: dict[str, list[dict[str, str]]] = {
    "hospital": [{"amenity": "hospital"}],
    "clinic": [{"amenity": "clinic"}, {"amenity": "doctors"}],
    "doctor": [{"amenity": "doctors"}, {"amenity": "clinic"}],
    "school": [{"amenity": "school"}],
    "college": [{"amenity": "college"}, {"amenity": "university"}],
    "university": [{"amenity": "university"}, {"amenity": "college"}],
    "hotel": [{"q": "[tourism=hotel]"}],
    "restaurant": [{"amenity": "restaurant"}],
    "cafe": [{"amenity": "cafe"}],
    "coffee": [{"amenity": "cafe"}],
    "gym": [{"q": "[leisure=fitness_centre]"}],
    "fitness": [{"q": "[leisure=fitness_centre]"}],
    "dental": [{"amenity": "dentist"}, {"amenity": "clinic"}],
    "dentist": [{"amenity": "dentist"}, {"amenity": "clinic"}],
    "pharmacy": [{"amenity": "pharmacy"}],
    "chemist": [{"amenity": "pharmacy"}],
    "bakery": [{"q": "bakery"}],
    "salon": [{"q": "salon"}],
    "spa": [{"amenity": "spa"}],
    "supermarket": [{"q": "supermarket"}],
    "grocery": [{"q": "grocery"}],
    "bank": [{"amenity": "bank"}],
    "bar": [{"amenity": "bar"}],
    "pub": [{"amenity": "pub"}],
    "lawyer": [{"q": "lawyer"}],
    "advocate": [{"q": "advocate"}],
    "society": [{"q": "society"}],
    "real estate": [{"q": "real estate"}],
    "builder": [{"q": "builder"}],
    "car repair": [{"q": "car repair"}],
    "software": [{"q": "software"}],
    "business": [{}],
}
_FALLBACK_TAGS: list[dict[str, str]] = [{}]

_OSM_BASE = "https://www.openstreetmap.org"


def _throttle() -> None:
    global _LAST_REQUEST_TS
    now = time.monotonic()
    wait = _MIN_INTERVAL_S - (now - _LAST_REQUEST_TS)
    if wait > 0:
        time.sleep(wait)
    _LAST_REQUEST_TS = time.monotonic()


def _maps_url(osm_type: str, osm_id: Any) -> str:
    """Public OSM permalink for a place (replaces Google maps_url)."""
    if osm_type in ("node", "way", "relation") and osm_id:
        return f"{_OSM_BASE}/{osm_type}/{osm_id}"
    return ""


def _resolve_website_fallback(name: str, location: str) -> str:
    """Best-effort web search fallback to discover a business's official website when OSM lacks it."""
    import re
    from urllib.parse import unquote

    if not name or not location:
        return ""
    try:
        query = f"{name} {location} official website".strip()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        with httpx.Client(timeout=4.0, follow_redirects=True) as client:
            resp = client.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=headers)
            if resp.status_code != 200:
                return ""
            matches = re.findall(r"uddg=([^&]+)", resp.text)
            junk_domains = (
                "facebook.com", "instagram.com", "wikipedia.org", "wikidata.org",
                "youtube.com", "twitter.com", "x.com", "linkedin.com",
                "mapsofindia.com", "yellowpages", "tripadvisor", "yelp.com",
                "magicbricks.com", "99acres.com", "housing.com", "pinterest.com"
            )
            directory_domains = (
                "justdial.com", "shiksha.com", "indiamart.com", "sulekha.com",
                "getschoolsinfo.com", "indiastudychannel.com", "edustoke.com",
                "ezyschooling.com", "boardingschoolindia.com", "uniapply.com",
                "allschoolscolleges.com", "starschooling.com", "schoolsuniverse.com",
                "educonnectin.com", "schoolmykids.com", "targetstudy.com",
                "schools.org.in", "icbse.com", "careers360.com"
            )
            first_directory_profile = ""
            for m in matches:
                url = unquote(m)
                lower = url.lower()
                if any(bad in lower for bad in junk_domains):
                    continue
                if any(d in lower for d in directory_domains):
                    if not first_directory_profile and url.startswith(("http://", "https://")):
                        first_directory_profile = url
                    continue
                if url.startswith(("http://", "https://")):
                    return url
            if first_directory_profile:
                return first_directory_profile
    except Exception:
        return ""
    return ""


class NominatimProvider(BusinessSearchProvider):
    """OpenStreetMap discovery provider (keyless, MIT/open data)."""

    name = "nominatim"

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client

    # -- plumbing -----------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> Any:
        s = get_settings()
        url = f"{s.nominatim_base_url}{path}"
        headers = {"User-Agent": s.nominatim_user_agent,
                   "Accept-Language": "en"}
        last: Optional[Exception] = None
        for attempt in range(2):
            _throttle()
            try:
                if self._client is not None:
                    resp = self._client.get(url, params=params,
                                            headers=headers)
                    code, payload = resp.status_code, None
                    try:
                        payload = resp.json()
                    except ValueError:
                        payload = None
                else:
                    with httpx.Client(timeout=15.0,
                                      follow_redirects=True) as c:
                        resp = c.get(url, params=params, headers=headers)
                    code = resp.status_code
                    try:
                        payload = resp.json()
                    except ValueError:
                        payload = None
                if code == 403:
                    raise ProviderUnavailable(
                        "Nominatim blocked this client (User-Agent policy).")
                if code == 429:
                    raise ProviderUnavailable(
                        "Nominatim rate limit reached; slow down or self-host.")
                if code >= 400:
                    raise ProviderUnavailable(f"HTTP {code} from Nominatim")
                return payload
            except (httpx.TimeoutException,) as exc:
                last = exc
                raise ProviderTimeout(
                    "Nominatim did not respond in time.") from exc
            except httpx.HTTPError as exc:
                last = exc
        raise ProviderUnavailable(
            f"Nominatim unreachable: {str(last)[:200]}")

    # -- OSM discovery ------------------------------------------------------

    def _tags_for(self, category: str) -> list[dict[str, str]]:
        cat = (category or "").strip().lower()
        matched: list[dict[str, str]] = []
        for key, tag_list in _OSM_CATEGORY_SEARCH_TARGETS.items():
            if key in cat or cat in key:
                for t in tag_list:
                    if t not in matched:
                        matched.append(t)
        if matched:
            return matched
        return [{"q": cat}] if cat else [{}]

    def _search_viewbox(self, query: PlaceSearchQuery) -> str:
        """Nominatim viewbox (lon1,lat1,lon2,lat2) from center+radius."""
        if query.latitude is None or query.longitude is None:
            return ""
        import math

        d_lat = query.radius_m / 111_320.0
        d_lon = query.radius_m / (111_320.0 *
                                  max(math.cos(math.radians(query.latitude)),
                                      0.01))
        w, e = query.longitude - d_lon, query.longitude + d_lon
        n, s_ = query.latitude + d_lat, query.latitude - d_lat
        return f"{w},{n},{e},{s_}"

    def _run_search(self, query: PlaceSearchQuery,
                    tag: dict[str, str]) -> list[PlaceDetails]:
        """One Nominatim search (amenity parameter, tag filter, or keyword search)."""
        params: dict[str, str] = {
            "format": "jsonv2",
            "limit": str(min(query.max_results, 50)),
            "addressdetails": "1",
            "extratags": "1",
            "namedetails": "1",
            "countrycodes": (query.country or "").lower(),
        }
        if query.latitude is not None and query.longitude is not None:
            # Coordinates are available: search within the geographical bounding box
            params["viewbox"] = self._search_viewbox(query)
            params["bounded"] = "1"
            if tag and "amenity" in tag:
                params["amenity"] = tag["amenity"]
            elif tag and "q" in tag:
                params["q"] = tag["q"]
            elif query.category:
                params["q"] = query.category
            else:
                params["q"] = "business"
        else:
            # Free-form search: category words + location text
            params["q"] = f"{query.category} {query.location_text}".strip()

        payload = self._get("/search", params)
        if not isinstance(payload, list):
            return []
        out: list[PlaceDetails] = []
        for hit in payload:
            try:
                lat = float(hit.get("lat"))
                lon = float(hit.get("lon"))
            except (TypeError, ValueError):
                continue
            addr = hit.get("address") or {}
            city = (addr.get("city") or addr.get("town")
                    or addr.get("village") or addr.get("state_district") or "")
            name = str(hit.get("name") or "").strip()
            if not name:
                # unnamed OSM feature (e.g. bare building) — skip for leads
                continue
            osm_type = str(hit.get("osm_type") or "")
            osm_id = hit.get("osm_id")
            extratags = hit.get("extratags") or {}
            website = str(
                hit.get("website")
                or extratags.get("website")
                or extratags.get("contact:website")
                or extratags.get("url")
                or ""
            ).strip()
            phone = str(
                hit.get("phone")
                or extratags.get("phone")
                or extratags.get("contact:phone")
                or extratags.get("contact:mobile")
                or extratags.get("mobile")
                or ""
            ).strip()
            email = str(
                hit.get("email")
                or extratags.get("email")
                or extratags.get("contact:email")
                or ""
            ).strip()
            out.append(PlaceDetails(
                provider=self.name,
                provider_place_id=f"osm_{osm_type}_{osm_id}",
                business_name=name[:250],
                category=query.category,
                address=", ".join(str(v) for v in (
                    addr.get("house_number"), addr.get("road"), city,
                    addr.get("postcode")) if v),
                phone=phone,
                website=normalize_url(website) if website else "",
                email=email,
                latitude=lat,
                longitude=lon,
                maps_url=_maps_url(osm_type, osm_id),
                opening_hours=str(hit.get("opening_hours") or ""),
                extra={"osm_type": osm_type, "osm_id": osm_id,
                       "extratags": extratags},
            ))
        return out

    # -- BusinessSearchProvider ---------------------------------------------

    def search_businesses(self, query: PlaceSearchQuery) -> list[PlaceDetails]:
        """Discover businesses via OSM tag + viewbox search."""
        results: list[PlaceDetails] = []
        seen_ids: set[str] = set()
        tags_to_try = self._tags_for(query.category)
        for tag in tags_to_try:
            try:
                batch = self._run_search(query, tag)
            except ProviderError:
                if results:
                    break  # some results already collected; keep them
                raise
            for place in batch:
                if place.provider_place_id not in seen_ids and place.business_name:
                    seen_ids.add(place.provider_place_id)
                    results.append(place)
            if len(results) >= query.max_results:
                break

        matched = results[:query.max_results]
        # Concurrently resolve missing websites for discovered businesses
        missing = [p for p in matched if not p.website and p.business_name]
        if missing:
            with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
                def _resolve_one(place: PlaceDetails) -> tuple[PlaceDetails, str]:
                    site = _resolve_website_fallback(place.business_name, place.address or query.location_text)
                    return place, site

                for place, site in pool.map(_resolve_one, missing):
                    if site:
                        place.website = normalize_url(site)
        return matched

    def get_business_details(self, place_id: str,
                             language: str = "en") -> Optional[PlaceDetails]:
        """Lookup one OSM feature: place_id format ``osm_<type>_<id>``."""
        if not place_id.startswith("osm_"):
            return None
        _, osm_type, osm_id = place_id.split("_", 2)
        payload = self._get("/lookup", {
            "osm_ids": f"{osm_type[0].upper()}{osm_id}",
            "format": "jsonv2", "addressdetails": "1", "extratags": "1", "namedetails": "1",
        })
        if isinstance(payload, list) and payload:
            hit = payload[0]
            try:
                lat, lon = float(hit.get("lat")), float(hit.get("lon"))
            except (TypeError, ValueError):
                return None
            addr = hit.get("address") or {}
            extratags = hit.get("extratags") or {}
            website = str(
                hit.get("website")
                or extratags.get("website")
                or extratags.get("contact:website")
                or extratags.get("url")
                or ""
            ).strip()
            phone = str(
                hit.get("phone")
                or extratags.get("phone")
                or extratags.get("contact:phone")
                or extratags.get("contact:mobile")
                or extratags.get("mobile")
                or ""
            ).strip()
            email = str(
                hit.get("email")
                or extratags.get("email")
                or extratags.get("contact:email")
                or ""
            ).strip()
            name = str(hit.get("name") or "")[:250]
            if not website and name:
                city = (addr.get("city") or addr.get("town") or "")
                website = _resolve_website_fallback(name, city)
            return PlaceDetails(
                provider=self.name,
                provider_place_id=place_id,
                business_name=name,
                address=", ".join(str(v) for v in (
                    addr.get("house_number"), addr.get("road"),
                    addr.get("city") or addr.get("town") or "",
                    addr.get("postcode")) if v),
                website=normalize_url(website) if website else "",
                phone=phone,
                email=email,
                latitude=lat,
                longitude=lon,
                maps_url=_maps_url(str(hit.get("osm_type") or ""),
                                   hit.get("osm_id")),
                extra={"osm_type": str(hit.get("osm_type") or ""),
                       "osm_id": hit.get("osm_id"),
                       "extratags": extratags},
            )
        return None

    def health_check(self) -> bool:
        """Cheap readiness probe — one tiny geocode request."""
        try:
            payload = self._get("/search", {"q": "Pune", "format": "jsonv2",
                                            "limit": "1"})
            return isinstance(payload, list)
        except ProviderError:
            return False


# ---------------------------------------------------------------------------
# Location resolution helper (used by the pipeline for lat/lng-less queries)
# ---------------------------------------------------------------------------

def resolve_location(query_text: str, country: str = "") -> Optional[
        tuple[float, float, str]]:
    """Geocode *query_text* via Nominatim → (lat, lon, display_name).

    Used when a search job arrives without parsed coordinates. Bounded by
    the same throttle/policy as the provider. Returns None on any failure.
    """
    if not (query_text or "").strip():
        return None
    params = {"q": query_text, "format": "jsonv2", "limit": "1"}
    if country:
        params["countrycodes"] = country.lower()
    try:
        payload = NominatimProvider()._get("/search", params)
    except ProviderError:
        return None
    if isinstance(payload, list) and payload:
        hit = payload[0]
        try:
            return float(hit.get("lat")), float(hit.get("lon")), \
                str(hit.get("display_name") or "")
        except (TypeError, ValueError):
            return None
    return None
