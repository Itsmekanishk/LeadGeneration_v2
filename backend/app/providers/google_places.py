"""Google Places API (New) provider.

Uses the official endpoints only:
- POST https://places.googleapis.com/v1/places:searchText
- GET  https://places.googleapis.com/v1/places/{place_id}

Field masks are explicit and selective (never ``*``) so we only receive — and
pay for — the fields the product needs. Temporary failures retry with
exponential backoff; auth errors and persistent failures raise ProviderError.
Every billable call is recorded to the ``api_usage`` table.
"""
from __future__ import annotations

from typing import Optional

import time
from datetime import datetime, timezone

import httpx

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.models.models import ApiUsage
from app.models.database import session_scope
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

log = event_logger("locallead.provider.google")

_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_PLACE_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Selective field masks — never use "*" in production.
_TEXT_SEARCH_FIELDS = [
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.location",
    "places.types",
    "places.googleMapsUri",
    "places.regularOpeningHours",
    "nextPageToken",
]

_DETAILS_FIELDS = [
    "id",
    "displayName",
    "formattedAddress",
    "nationalPhoneNumber",
    "internationalPhoneNumber",
    "websiteUri",
    "rating",
    "userRatingCount",
    "location",
    "types",
    "googleMapsUri",
    "regularOpeningHours",
]

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5


class GooglePlacesProvider(BusinessSearchProvider):
    """Official Google Places API (New) client."""

    name = "google"

    def __init__(self, api_key: Optional[str] = None, user_id: Optional[str] = None,
                 job_id: Optional[str] = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.google_maps_api_key
        if not self._api_key:
            raise ProviderAuthError(
                "GOOGLE_MAPS_API_KEY is not configured; cannot use the Google provider."
            )
        self._user_id = user_id
        self._job_id = job_id
        self._language = settings.default_language
        self._timeout = httpx.Timeout(15.0, connect=5.0)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _headers(self, field_mask: list[str]) -> dict[str, str]:
        return {
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": " ".join(field_mask),
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, *, headers: dict,
                 json_body: Optional[dict] = None, params: Optional[dict] = None) -> httpx.Response:
        """HTTP with exponential backoff on retryable failures."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.request(method, url, headers=headers, json=json_body, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = ProviderUnavailable(
                        f"provider http {resp.status_code}"
                    )
                    if resp.status_code == 429:
                        last_exc = ProviderRateLimited("provider rate limit reached")
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else _BACKOFF_BASE ** attempt
                    log.warning(
                        "provider_retryable_error",
                        attempt=attempt,
                        status=resp.status_code,
                        delay_s=delay,
                    )
                    time.sleep(min(delay, 30))
                    continue
                if resp.status_code in (401, 403):
                    raise ProviderAuthError(
                        f"Google Places rejected credentials (http {resp.status_code})"
                    )
                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = ProviderTimeout(f"provider timeout: {exc}") if isinstance(
                    exc, httpx.TimeoutException) else ProviderUnavailable(
                    f"provider connect error: {exc}")
                time.sleep(_BACKOFF_BASE ** attempt)
            except (ProviderAuthError, ProviderRateLimited):
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # Client errors are deterministic: retrying will not help.
                if status in (404, 410):
                    raise ProviderUnavailable(
                        f"provider resource not found (http {status})"
                    ) from exc
                raise ProviderError(
                    f"provider rejected the request (http {status})"
                ) from exc
            except httpx.HTTPError as exc:
                last_exc = ProviderUnavailable(f"provider http error: {exc}")
                time.sleep(_BACKOFF_BASE ** attempt)
        raise last_exc or ProviderUnavailable("provider request failed")

    def _record_usage(self, endpoint: str) -> None:
        """Persist a billable API call for the usage dashboard."""
        settings = get_settings()
        cost = (
            settings.google_places_text_search_cost_per_call
            if endpoint == "places:searchText"
            else settings.google_places_details_cost_per_call
        )
        try:
            with session_scope() as db:
                db.add(ApiUsage(
                    provider=self.name,
                    endpoint=endpoint,
                    request_count=1,
                    estimated_cost=cost,
                    user_id=self._user_id,
                    job_id=self._job_id,
                ))
        except Exception:  # noqa: BLE001 — usage tracking must never break search
            log.error("api_usage_record_failed", endpoint=endpoint)

    # ------------------------------------------------------------------ #
    # BusinessSearchProvider interface
    # ------------------------------------------------------------------ #

    def search_businesses(self, query: PlaceSearchQuery) -> list[PlaceDetails]:
        """Page through Text Search (New) until max_results or exhaustion."""
        results: list[PlaceDetails] = []
        page_token: Optional[str] = None
        seen_pages = 0

        text = f"{query.category} in {query.location_text}".strip()
        body: dict = {
            "textQuery": text,
            "languageCode": query.language or self._language,
            "regionCode": (query.country or "in").lower(),
            "maxResultCount": min(20, max(1, query.max_results)),
        }
        if query.latitude is not None and query.longitude is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": query.latitude, "longitude": query.longitude},
                    "radius": query.radius_m,
                }
            }

        while len(results) < query.max_results and seen_pages < 6:
            if page_token:
                body["pageToken"] = page_token
                body.pop("locationBias", None)  # token pages must repeat request w/o bias changes
            resp = self._request(
                "POST", _TEXT_SEARCH_URL,
                headers=self._headers(_TEXT_SEARCH_FIELDS),
                json_body=body,
            )
            self._record_usage("places:searchText")
            try:
                data = resp.json()
            except ValueError as exc:
                raise ProviderUnavailable(
                    "Google Places returned a malformed (non-JSON) response."
                ) from exc
            if not isinstance(data, dict):
                raise ProviderUnavailable(
                    "Google Places returned an unexpected response shape."
                )
            for place in data.get("places", []):
                results.append(self._map_place(place))
                if len(results) >= query.max_results:
                    break
            page_token = data.get("nextPageToken")
            seen_pages += 1
            if not page_token:
                break
            time.sleep(2)  # Google requires a short delay before using next page token

        return results[: query.max_results]

    def get_business_details(self, place_id: str, language: str = "en") -> Optional[PlaceDetails]:
        """Fetch one place by id via Place Details (New)."""
        url = _PLACE_URL.format(place_id=place_id)
        try:
            resp = self._request(
                "GET", url,
                headers=self._headers(_DETAILS_FIELDS),
                params={"languageCode": language or self._language},
            )
        except ProviderError as exc:
            log.warning("provider_details_failed", place_id=place_id, error=str(exc))
            return None
        self._record_usage("places:details")
        try:
            payload = resp.json()
        except ValueError:
            log.warning("provider_details_malformed", place_id=place_id)
            return None
        if not isinstance(payload, dict):
            return None
        return self._map_place(payload)

    def health_check(self) -> bool:
        """True when an API key is present. No billable call is made."""
        return bool(self._api_key)

    # ------------------------------------------------------------------ #

    def _empty_place(self) -> PlaceDetails:
        """A blank normalized record for malformed/partial provider payloads."""
        from datetime import datetime, timezone

        return PlaceDetails(
            provider=self.name,
            provider_place_id="",
            business_name="",
            source_timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _map_place(self, place: dict) -> PlaceDetails:
        """Map a Google Places (New) payload to the normalized record.

        Defensive against partial payloads: a malformed place never breaks the
        whole search, it just yields empty optional fields.
        """
        if not isinstance(place, dict):
            return self._empty_place()
        location = place.get("location") or {}
        opening = place.get("regularOpeningHours") or {}
        weekday = opening.get("weekdayDescriptions") or []
        display = place.get("displayName") or {}
        types = place.get("types") or []
        if not isinstance(display, dict):
            display = {}
        if not isinstance(types, list):
            types = []
        return PlaceDetails(
            provider=self.name,
            provider_place_id=place.get("id", ""),
            business_name=display.get("text", "") or "",
            category=(types[0].replace("_", " ").title() if types else ""),
            address=place.get("formattedAddress", "") or "",
            phone=place.get("internationalPhoneNumber")
            or place.get("nationalPhoneNumber", "")
            or "",
            website=place.get("websiteUri", "") or "",
            rating=place.get("rating"),
            review_count=place.get("userRatingCount"),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            maps_url=place.get("googleMapsUri", "") or "",
            opening_hours="; ".join(weekday)[:1000],
            source_timestamp=datetime.now(timezone.utc).isoformat(),
        )
