# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Geoapify Places API provider.

Provides high-accuracy global places discovery, geocoding, and contact enrichment
using the official Geoapify endpoints:
- GET https://api.geoapify.com/v2/places
- GET https://api.geoapify.com/v2/place-details
- GET https://api.geoapify.com/v1/geocode/search

Includes built-in category translation, proximity/circle filtering,
coordinate resolving, pagination, and retry logic with exponential backoff.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.models.database import session_scope
from app.models.models import ApiUsage
from app.providers.base import (
    BusinessSearchProvider,
    PlaceDetails,
    PlaceSearchQuery,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.utils.normalization import normalize_url

log = event_logger("locallead.provider.geoapify")

_GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"
_GEOAPIFY_DETAILS_URL = "https://api.geoapify.com/v2/place-details"
_GEOAPIFY_GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"

# Common search category -> Geoapify supported categories
_GEOAPIFY_CATEGORY_MAP: dict[str, str] = {
    "hospital": "healthcare.hospital",
    "clinic": "healthcare",
    "doctor": "healthcare",
    "dentist": "healthcare.dentist",
    "dental": "healthcare.dentist",
    "pharmacy": "healthcare.pharmacy",
    "chemist": "healthcare.pharmacy",
    "school": "education.school",
    "college": "education.college",
    "university": "education.university",
    "hotel": "accommodation.hotel",
    "restaurant": "catering.restaurant",
    "cafe": "catering.cafe",
    "coffee": "catering.cafe",
    "bar": "catering.bar",
    "pub": "catering.bar",
    "gym": "sport.fitness",
    "fitness": "sport.fitness",
    "bakery": "commercial.food_and_drink.bakery",
    "salon": "service.beauty",
    "spa": "service.beauty.spa",
    "supermarket": "commercial.supermarket",
    "grocery": "commercial.food_and_drink",
    "bank": "service.financial.bank",
    "lawyer": "office.lawyer",
    "advocate": "office.lawyer",
    "real estate": "office",
    "builder": "office.company",
    "car repair": "service.vehicle",
    "software": "office.company",
}

_TOP_LEVEL_CATEGORIES: set[str] = {
    "catering",
    "commercial",
    "healthcare",
    "education",
    "service",
    "accommodation",
    "sport",
    "office",
    "entertainment",
    "tourism",
}

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5


class GeoapifyPlacesProvider(BusinessSearchProvider):
    """Geoapify Places API provider for high-accuracy discovery."""

    name = "geoapify"

    def __init__(
        self,
        api_key: Optional[str] = None,
        user_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self._api_key = (api_key or settings.geoapify_api_key).strip()
        if not self._api_key:
            raise ProviderAuthError(
                "GEOAPIFY_API_KEY is not configured; cannot use the Geoapify provider."
            )
        self._user_id = user_id
        self._job_id = job_id
        self._language = settings.default_language
        self._timeout = httpx.Timeout(15.0, connect=5.0)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        """HTTP request with exponential backoff on retryable failures."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.request(method, url, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = ProviderUnavailable(
                        f"Geoapify http {resp.status_code}"
                    )
                    if resp.status_code == 429:
                        last_exc = ProviderRateLimited("Geoapify rate limit reached")
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else _BACKOFF_BASE ** attempt
                    log.warning(
                        "geoapify_retryable_error",
                        attempt=attempt,
                        status=resp.status_code,
                        delay_s=delay,
                    )
                    time.sleep(min(delay, 10))
                    continue
                if resp.status_code in (401, 403):
                    raise ProviderAuthError(
                        f"Geoapify rejected credentials (http {resp.status_code})"
                    )
                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = (
                    ProviderTimeout(f"Geoapify timeout: {exc}")
                    if isinstance(exc, httpx.TimeoutException)
                    else ProviderUnavailable(f"Geoapify connect error: {exc}")
                )
                time.sleep(_BACKOFF_BASE ** attempt)
            except (ProviderAuthError, ProviderRateLimited):
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (404, 410):
                    raise ProviderUnavailable(
                        f"Geoapify resource not found (http {status})"
                    ) from exc
                raise ProviderError(
                    f"Geoapify rejected the request (http {status}): {exc.response.text[:200]}"
                ) from exc
            except httpx.HTTPError as exc:
                last_exc = ProviderUnavailable(f"Geoapify http error: {exc}")
                time.sleep(_BACKOFF_BASE ** attempt)
        raise last_exc or ProviderUnavailable("Geoapify request failed")

    def _record_usage(self, endpoint: str) -> None:
        """Persist API call for usage tracking."""
        try:
            with session_scope() as db:
                db.add(
                    ApiUsage(
                        provider=self.name,
                        endpoint=endpoint,
                        request_count=1,
                        estimated_cost=0.0,
                        user_id=self._user_id,
                        job_id=self._job_id,
                    )
                )
        except Exception:  # noqa: BLE001
            log.error("api_usage_record_failed", provider=self.name, endpoint=endpoint)

    def _resolve_coordinates(
        self, location_text: str, country: str = "in"
    ) -> Optional[tuple[float, float]]:
        """Geocode a text location to (lat, lon) coordinates using Geoapify."""
        if not location_text:
            return None
        params: dict[str, Any] = {
            "text": location_text,
            "limit": 1,
            "apiKey": self._api_key,
        }
        if country:
            params["filter"] = f"countrycode:{country.lower().strip()}"
        try:
            resp = self._request("GET", _GEOAPIFY_GEOCODE_URL, params=params)
            data = resp.json()
            features = data.get("features", [])
            if features:
                coords = features[0].get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    lon, lat = float(coords[0]), float(coords[1])
                    return lat, lon
        except Exception as exc:
            log.warning("geoapify_geocode_fallback", error=str(exc))
        return None

    def _map_category(self, category: str) -> tuple[str, Optional[str]]:
        """Map user category string to (geoapify_category, optional_name_filter)."""
        raw = (category or "").lower().strip()
        if not raw:
            return "commercial,catering,service,office", None

        # Exact match in category mapping
        if raw in _GEOAPIFY_CATEGORY_MAP:
            return _GEOAPIFY_CATEGORY_MAP[raw], None

        # Partial match in keys
        for key, mapped in _GEOAPIFY_CATEGORY_MAP.items():
            if key in raw or raw in key:
                return mapped, None

        # Top-level category match
        for top in _TOP_LEVEL_CATEGORIES:
            if top in raw:
                return top, None

        # Fallback to broad business categories with name filter
        return "commercial,catering,service,office,healthcare", raw

    def _map_feature_to_place(self, feature: dict[str, Any]) -> PlaceDetails:
        """Transform a Geoapify GeoJSON feature into normalized PlaceDetails."""
        props = feature.get("properties", {}) or {}
        geometry = feature.get("geometry", {}) or {}
        coords = geometry.get("coordinates", []) if geometry else []
        lon = coords[0] if len(coords) > 0 else props.get("lon")
        lat = coords[1] if len(coords) > 1 else props.get("lat")

        place_id = props.get("place_id") or f"geoapify_{lat}_{lon}"
        name = props.get("name") or props.get("address_line1") or "Unknown Business"

        # Construct readable address
        address = props.get("formatted") or ", ".join(
            filter(
                None,
                [
                    props.get("address_line1"),
                    props.get("address_line2"),
                    props.get("city"),
                    props.get("state"),
                    props.get("postcode"),
                    props.get("country"),
                ],
            )
        )

        contact = props.get("contact", {}) or {}
        datasource = props.get("datasource", {}) or {}
        raw = datasource.get("raw", {}) or {}

        phone = contact.get("phone") or props.get("phone") or raw.get("phone") or ""
        website = props.get("website") or contact.get("website") or raw.get("website") or ""
        email = contact.get("email") or props.get("email") or raw.get("email") or ""
        opening_hours = props.get("opening_hours") or raw.get("opening_hours") or ""
        name_str = str(name).strip() if name is not None else ""
        address_str = str(address).strip() if address is not None else ""
        phone_str = str(phone).strip() if phone is not None else ""
        website_str = str(website).strip() if website is not None else ""
        email_str = str(email).strip().lower() if email is not None else ""
        opening_hours_str = str(opening_hours).strip() if opening_hours is not None else ""

        cats = props.get("categories", [])
        category_str = ", ".join(cats) if isinstance(cats, list) else str(cats)

        maps_url = ""
        if lat is not None and lon is not None:
            maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

        return PlaceDetails(
            provider=self.name,
            provider_place_id=str(place_id),
            business_name=name_str,
            category=category_str[:255],
            address=address_str[:512],
            phone=phone_str[:32],
            website=normalize_url(website_str) if website_str else "",
            email=email_str[:255],
            rating=None,
            review_count=None,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            maps_url=maps_url,
            opening_hours=opening_hours_str[:1024],
            source_timestamp=datetime.now(timezone.utc).isoformat(),
            extra={
                "datasource": datasource.get("sourcename", "geoapify"),
                "place_id": place_id,
                "city": str(props.get("city", "")),
                "postcode": str(props.get("postcode", "")),
            },
        )

    # ------------------------------------------------------------------ #
    # BusinessSearchProvider interface
    # ------------------------------------------------------------------ #

    def search_businesses(self, query: PlaceSearchQuery) -> list[PlaceDetails]:
        """Search businesses using Geoapify Places API."""
        lat = query.latitude
        lon = query.longitude

        # Resolve coordinates if not provided
        if lat is None or lon is None:
            coords = self._resolve_coordinates(query.location_text, query.country)
            if coords:
                lat, lon = coords
            else:
                # Fallback to Nominatim location resolver if enabled
                settings = get_settings()
                if settings.enable_nominatim_geocode:
                    from app.providers.nominatim import resolve_location

                    nom_res = resolve_location(query.location_text, query.country)
                    if nom_res:
                        lat, lon = nom_res.latitude, nom_res.longitude

        if lat is None or lon is None:
            raise ProviderError(
                f"Could not resolve geographic location for '{query.location_text}'"
            )

        categories_str, name_filter = self._map_category(query.category)
        radius = query.radius_m or 3000

        results: list[PlaceDetails] = []
        offset = 0
        seen_places: set[str] = set()
        page_size = min(50, query.max_results)

        while len(results) < query.max_results and offset < 200:
            params: dict[str, Any] = {
                "categories": categories_str,
                "filter": f"circle:{lon},{lat},{radius}",
                "bias": f"proximity:{lon},{lat}",
                "limit": page_size,
                "offset": offset,
                "lang": query.language or self._language,
                "apiKey": self._api_key,
            }
            if name_filter:
                params["name"] = name_filter

            resp = self._request("GET", _GEOAPIFY_PLACES_URL, params=params)
            self._record_usage("places")

            try:
                data = resp.json()
            except ValueError as exc:
                raise ProviderUnavailable(
                    "Geoapify returned a malformed (non-JSON) response."
                ) from exc

            features = data.get("features", [])
            if not features:
                break

            for feature in features:
                place = self._map_feature_to_place(feature)
                if place.provider_place_id in seen_places:
                    continue
                seen_places.add(place.provider_place_id)
                results.append(place)
                if len(results) >= query.max_results:
                    break

            offset += len(features)
            if len(features) < page_size:
                break

        return results[: query.max_results]

    def get_business_details(
        self, place_id: str, language: str = "en"
    ) -> Optional[PlaceDetails]:
        """Fetch full details for one place by Geoapify place_id."""
        params = {
            "id": place_id,
            "lang": language or self._language,
            "apiKey": self._api_key,
        }
        try:
            resp = self._request("GET", _GEOAPIFY_DETAILS_URL, params=params)
            self._record_usage("place-details")
            data = resp.json()
            features = data.get("features", [])
            if features:
                return self._map_feature_to_place(features[0])
        except Exception as exc:
            log.warning("geoapify_details_failed", place_id=place_id, error=str(exc))
        return None

    def health_check(self) -> bool:
        """Probe Geoapify availability. Validates API key presence."""
        return bool(self._api_key)
