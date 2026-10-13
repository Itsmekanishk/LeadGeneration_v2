# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Geocoding via OpenStreetMap — zero Google Maps endpoints.

Primary: **Nominatim** (https://nominatim.openstreetmap.org) — free for
reasonable use under its usage policy (honest User-Agent, ≥ 1 req/s, no
bulk). Query strategy per the master prompt: *business name + city* first,
then city/area alone.

Fallback: **Open-Meteo geocoding** (https://geocoding-api.open-meteo.com) —
a free, keyless city-level geocoder used when Nominatim returns no result
(e.g. rural areas missing from OSM). It replaced the previously specified
REST Countries fallback, whose free API was deprecated and now requires a
paid key.

Every call is audit-logged (service="nominatim" / "open-meteo").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

from leadgen import audit
from leadgen.config import get_settings


@dataclass
class GeoResult:
    """Resolved coordinates with provenance (never from Google)."""

    latitude: float | None = None
    longitude: float | None = None
    display_name: str = ""
    source: str = ""          # nominatim | restcountries | ""
    region: str = ""          # OSM country/region info
    raw: dict = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def _nominatim_search(q: str, country_code: str = "") -> GeoResult:
    """One Nominatim /search call (structured params, JSON, bounded)."""
    s = get_settings()
    params: dict[str, str] = {
        "q": q, "format": "jsonv2", "limit": "1", "addressdetails": "1",
    }
    if country_code:
        params["countrycodes"] = country_code.lower().strip()
    result = audit.http_get(f"{s.nominatim_base_url}/search",
                            params=params, api_exempt=True)
    row = audit.audit_record(result, "nominatim", {"query": q[:200]})
    geo = GeoResult(audit=[row], raw=result.json if result.ok else {})
    if result.ok and isinstance(result.json, list) and result.json:
        hit = result.json[0]
        try:
            geo.latitude = float(hit.get("lat"))
            geo.longitude = float(hit.get("lon"))
        except (TypeError, ValueError):
            return geo
        geo.display_name = str(hit.get("display_name") or "")
        addr = hit.get("address") or {}
        geo.region = ", ".join(
            str(addr[k]) for k in ("country", "state", "city") if addr.get(k))
    return geo


def geocode(business_name: str, city: str = "",
            country_code: str = "") -> GeoResult:
    """Resolve *business name + city* to latitude/longitude via OSM.

    Tries the full "name, city" string first, then the city/area alone.
    When Nominatim finds nothing, REST Countries provides regional
    (country-level) coordinates as the documented fallback.
    """
    name = (business_name or "").strip().strip(",")
    city_clean = (city or "").strip().strip(",")
    country_clean = (country_code or "").strip()

    queries: list[str] = []
    if name and city_clean:
        queries.append(f"{name}, {city_clean}")
    if city_clean:
        queries.append(city_clean)
    if name and not city_clean:
        queries.append(name)
    if not queries:
        return GeoResult()

    audits: list[dict] = []
    for q in queries:
        geo = _nominatim_search(q, country_clean)
        audits += geo.audit
        if geo.found:
            geo.audit = audits
            geo.source = "nominatim"
            return geo

    # ---- Fallback: Open-Meteo city-level geocoding ------------------------
    if city_clean:
        params = {"name": city_clean, "count": "1", "language": "en",
                  "format": "json"}
        if country_clean and len(country_clean) == 2:
            params["countryCode"] = country_clean.lower()
        result = audit.http_get(
            f"https://geocoding-api.open-meteo.com/v1/search",
            params=params, api_exempt=True,
        )
        row = audit.audit_record(result, "open-meteo",
                                 {"query": city_clean})
        audits.append(row)
        if result.ok and isinstance(result.json, dict):
            hits = result.json.get("results") or []
            if hits:
                hit = hits[0]
                try:
                    lat, lon = float(hit.get("latitude")), \
                        float(hit.get("longitude"))
                except (TypeError, ValueError):
                    return GeoResult(audit=audits)
                parts = [str(hit.get(k)) for k in
                         ("name", "admin1", "country") if hit.get(k)]
                return GeoResult(
                    latitude=lat, longitude=lon,
                    display_name=", ".join(parts),
                    region=", ".join(str(x) for x in (
                        hit.get("country"), hit.get("admin1")) if x),
                    source="open-meteo", audit=audits,
                )
    return GeoResult(audit=audits)
