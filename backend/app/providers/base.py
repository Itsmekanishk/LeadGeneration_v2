"""BusinessSearchProvider abstraction.

The rest of the application depends only on the interface and the normalized
dataclasses in this module — never on a concrete provider. Swap providers via
``app.providers.factory`` without touching pipeline code.

Compliance note: concrete providers must only use official APIs or data
sources where automated access is permitted by the provider's terms.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional, Any

from pydantic import BaseModel, Field


class ProviderError(RuntimeError):
    """Base class for provider failures (auth, quota, network, bad response)."""


class ProviderAuthError(ProviderError):
    """API key invalid / missing permissions."""


class ProviderRateLimited(ProviderError):
    """Provider returned 429 / quota exceeded."""


class ProviderTimeout(ProviderError):
    """Provider did not answer in time."""


class ProviderUnavailable(ProviderError):
    """5xx / network failure talking to the provider."""


class ProviderNotConfigured(ProviderError):
    """Provider selected but its credentials are absent from the environment.

    Permanent configuration problem — retrying will never help, so the user
    message differs from the transient failures.
    """


# User-safe messages per failure class (raw exceptions stay in server logs).
_FRIENDLY_PROVIDER_MESSAGES: list[tuple[type[Exception], str]] = [
    (ProviderNotConfigured, "The selected data source is not configured on the server. Please choose a different data source or contact support."),
    (ProviderAuthError, "Business data provider authentication failed. Please contact support."),
    (ProviderRateLimited, "Business data provider quota has been reached. Please try again later."),
    (ProviderTimeout, "The business data provider took too long to respond. Please retry."),
    (ProviderUnavailable, "Temporary provider error. Please retry in a few minutes."),
]


def friendly_provider_message(exc: Exception) -> str:
    """Map a provider exception to a user-safe message (no stack details)."""
    for exc_type, message in _FRIENDLY_PROVIDER_MESSAGES:
        if isinstance(exc, exc_type):
            return message
    return "The search could not be completed due to a provider error. Please retry."


class PlaceSearchQuery(BaseModel):
    """A resolved discovery query handed to a provider."""

    category: str = Field(min_length=1, description='e.g. "hospital"')
    location_text: str = Field(min_length=1, description='e.g. "Porwal Road, Pune"')
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    radius_m: int = Field(default=3000, ge=100, le=50000)
    max_results: int = Field(default=50, ge=1, le=200)
    country: str = "in"
    language: str = "en"


class PlaceDetails(BaseModel):
    """Normalized business record returned by any provider.

    Only fields the product actually needs — providers map from their native
    payloads into this shape and must not request more than required.
    """

    provider: str
    provider_place_id: str
    business_name: str
    category: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    email: str = ""
    rating: Optional[float] = None
    review_count: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    maps_url: str = ""
    opening_hours: str = ""
    source_timestamp: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


class BusinessSearchProvider(abc.ABC):
    """Interface every business-data provider must implement."""

    name: str = "abstract"

    @abc.abstractmethod
    def search_businesses(self, query: PlaceSearchQuery) -> list[PlaceDetails]:
        """Discover businesses matching *query*. Returns normalized records."""

    @abc.abstractmethod
    def get_business_details(self, place_id: str, language: str = "en") -> Optional[PlaceDetails]:
        """Fetch full details for a single place id (or None if unavailable)."""

    @abc.abstractmethod
    def health_check(self) -> bool:
        """Cheap readiness probe; must not consume billable quota."""
