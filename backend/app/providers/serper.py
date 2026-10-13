"""Serper API provider.

Uses the https://google.serper.dev API to search for businesses.
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

log = event_logger("locallead.provider.serper")

_SEARCH_URL = "https://google.serper.dev/places"
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5


class SerperProvider(BusinessSearchProvider):
    """Serper API client."""

    name = "serper"

    def __init__(self, api_key: Optional[str] = None, user_id: Optional[str] = None,
                 job_id: Optional[str] = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.serper_api_key
        if not self._api_key:
            raise ProviderAuthError(
                "SERPER_API_KEY is not configured; cannot use the Serper provider."
            )
        self._user_id = user_id
        self._job_id = job_id
        self._timeout = httpx.Timeout(15.0, connect=5.0)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-KEY": self._api_key,
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
                        f"Serper API rejected credentials (http {resp.status_code})"
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
        try:
            with session_scope() as db:
                db.add(ApiUsage(
                    provider=self.name,
                    endpoint=endpoint,
                    request_count=1,
                    estimated_cost=0.001, # Approximate cost per Serper call
                    user_id=self._user_id,
                    job_id=self._job_id,
                ))
        except Exception:
            log.error("api_usage_record_failed", endpoint=endpoint)

    # ------------------------------------------------------------------ #
    # BusinessSearchProvider interface
    # ------------------------------------------------------------------ #

    def search_businesses(self, query: PlaceSearchQuery) -> list[PlaceDetails]:
        """Search businesses using Serper Places API."""
        text = f"{query.category} in {query.location_text}".strip()
        body: dict = {
            "q": text,
        }
        
        if query.country:
            body["gl"] = query.country
        if query.language:
            body["hl"] = query.language

        resp = self._request(
            "POST", _SEARCH_URL,
            headers=self._headers(),
            json_body=body,
        )
        self._record_usage("places")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderUnavailable(
                "Serper returned a malformed (non-JSON) response."
            ) from exc
        if not isinstance(data, dict):
            raise ProviderUnavailable(
                "Serper returned an unexpected response shape."
            )

        results: list[PlaceDetails] = []
        places = data.get("places", [])
        if not isinstance(places, list):
            places = []

        for place in places:
            results.append(self._map_place(place))
            if len(results) >= query.max_results:
                break

        return results

    def get_business_details(self, place_id: str, language: str = "en") -> Optional[PlaceDetails]:
        """Serper places endpoint usually returns enough details in the search phase."""
        return None

    def health_check(self) -> bool:
        """True when an API key is present."""
        return bool(self._api_key)

    # ------------------------------------------------------------------ #

    def _map_place(self, place: dict) -> PlaceDetails:
        """Map a Serper Places payload to the normalized record.

        Defensive against partial payloads: a malformed place never breaks the
        whole search, it just yields empty optional fields.
        """
        if not isinstance(place, dict):
            place = {}
        return PlaceDetails(
            provider=self.name,
            provider_place_id=place.get("cid", "") or place.get("placeId", ""),
            business_name=place.get("title", ""),
            category=place.get("category", ""),
            address=place.get("address", ""),
            phone=place.get("phoneNumber", ""),
            website=place.get("website", ""),
            rating=place.get("rating"),
            review_count=place.get("ratingCount"),
            latitude=place.get("latitude"),
            longitude=place.get("longitude"),
            maps_url=f"https://www.google.com/maps/place/?q=place_id:{place.get('cid', '')}",
            opening_hours="",
            source_timestamp=datetime.now(timezone.utc).isoformat(),
        )
