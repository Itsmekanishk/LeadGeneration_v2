"""Provider factory: the ONLY place that knows about concrete providers.

Supports side-by-side providers (e.g. Google + Serper both configured): when
the primary provider fails at runtime (auth, rate limit, outage), the factory
can transparently retry the search on the next provider in the fallback chain
(``PROVIDER_FALLBACK_CHAIN=auto`` by default).
"""
from __future__ import annotations

from typing import Optional

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.providers.base import (
    BusinessSearchProvider,
    ProviderError,
    ProviderNotConfigured,
    PlaceDetails,
    PlaceSearchQuery,
)
from app.providers.mock_provider import MockBusinessProvider

log = event_logger("locallead.provider.factory")

_PROVIDERS: dict[str, type[BusinessSearchProvider]] = {
    "mock": MockBusinessProvider,
    "google": None,  # imported lazily so httpx payloads stay optional in tests
    "serper": None,
    "nominatim": None,  # OpenStreetMap — keyless, imported lazily
    "geoapify": None,  # Geoapify Places API — imported lazily
}


def register_provider(name: str, cls: type[BusinessSearchProvider]) -> None:
    """Register a custom provider (extension point)."""
    _PROVIDERS[name] = cls


def get_provider(name: Optional[str] = None, user_id: Optional[str] = None,
                 job_id: Optional[str] = None) -> BusinessSearchProvider:
    """Instantiate the configured provider.

    Raises ProviderError when a production provider is selected but not
    configured correctly (missing credentials).
    """
    settings = get_settings()
    raw_name = (name or settings.business_provider).lower().strip()
    provider_name = settings.business_provider.lower().strip() if raw_name in ("auto", "") else raw_name
    if provider_name not in _PROVIDERS:
        raise ProviderError(f"Unknown business provider '{provider_name}'")
    cls = _PROVIDERS[provider_name]
    if cls is None:
        if provider_name == "google":
            from app.providers.google_places import GooglePlacesProvider

            cls = GooglePlacesProvider
        elif provider_name == "serper":
            from app.providers.serper import SerperProvider

            cls = SerperProvider
        elif provider_name == "nominatim":
            from app.providers.nominatim import NominatimProvider

            cls = NominatimProvider
        elif provider_name == "geoapify":
            from app.providers.geoapify import GeoapifyPlacesProvider

            cls = GeoapifyPlacesProvider
    try:
        return cls(user_id=user_id, job_id=job_id)  # type: ignore[call-arg]
    except TypeError:
        return cls()  # type: ignore[call-arg]


def _provider_configured(name: str) -> bool:
    """True when the named provider has usable credentials/config."""
    s = get_settings()
    if name == "google":
        return bool(s.google_maps_api_key)
    if name == "serper":
        return bool(s.serper_api_key)
    if name == "geoapify":
        return bool(s.geoapify_api_key)
    if name == "nominatim":
        return True  # keyless open service
    if name == "mock":
        return True
    return False


def search_with_fallback(
    provider_name: str,
    query: PlaceSearchQuery,
    *,
    user_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> tuple[list[PlaceDetails], str, Optional[ProviderError]]:
    """Run discovery on *provider_name*, falling back down the chain on failure.

    Returns ``(results, provider_actually_used, last_error)``. ``last_error``
    is None when the primary succeeded.
    """
    settings = get_settings()
    chain = settings.fallback_chain
    attempted: list[str] = []
    last_error: Optional[ProviderError] = None

    # Resolve 'auto' or empty to the system default provider
    req_name = (provider_name or settings.business_provider).lower().strip()
    if req_name in ("auto", ""):
        req_name = settings.business_provider.lower().strip()

    # Always try the requested provider first, followed by fallbacks.
    order = [req_name] + [p for p in chain if p != req_name and p != "auto"]
    for name in order:
        if name in attempted:
            continue
        # NEVER silently substitute demo data for a real-data request:
        # mock is only usable when it was explicitly requested.
        if name == "mock" and req_name != "mock":
            log.warning("provider_mock_fallback_refused", requested=req_name)
            continue
        # Skip providers that are not configured.
        if name != "mock" and not _provider_configured(name):
            log.warning("provider_fallback_skipped_unconfigured", provider=name)
        else:
            attempted.append(name)
            try:
                provider = get_provider(name, user_id=user_id, job_id=job_id)
                results = provider.search_businesses(query)
                if name != provider_name:
                    log.warning(
                        "provider_fallback_used",
                        requested=provider_name,
                        used=name,
                        results=len(results),
                    )
                return results, name, None
            except ProviderError as exc:
                last_error = exc
                log.error(
                    "provider_attempt_failed",
                    provider=name,
                    job_id=job_id,
                    error=str(exc)[:300],
                )

    if not attempted:
        raise ProviderNotConfigured(
            f"No configured provider available for '{provider_name}' and no "
            "fallback candidates. Check provider API keys in .env."
        )
    return [], provider_name, last_error
