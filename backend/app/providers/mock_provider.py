"""Deterministic mock provider — no network, no credentials, no quota.

Supports synthetic scenarios useful in tests via the location string:
- include "timeout"  → raises ProviderTimeout
- include "empty"    → returns zero results
"""
from __future__ import annotations

from typing import Optional

from datetime import datetime, timezone

from app.providers.base import (
    BusinessSearchProvider,
    PlaceDetails,
    PlaceSearchQuery,
    ProviderTimeout,
)

_NAMES = ["Sunrise", "Apex", "Nova", "Prime", "Elite", "Everest", "Orbit", "Vertex",
          "Horizon", "Summit", "Pinnacle", "Zenith", "Aurora", "Cascade", "Quantum",
          "Meridian", "Beacon", "Harbor", "Lakeside", "Crescent"]
_SUFFIX = {
    "hospital": "Hospital", "clinic": "Clinic", "school": "Public School",
    "society": "Society", "hotel": "Hotel", "dental": "Dental Clinic",
    "gym": "Fitness", "restaurant": "Kitchen", "cafe": "Cafe",
}
_STREETS = ["Porwal Road", "Viman Nagar Road", "Kharadi Bypass Road", "MG Road",
            "FC Road", "Baner Road", "Aundh Road", "Wakad Road"]
_AREAS = ["Porwal Road, Pune", "Viman Nagar, Pune", "Kharadi, Pune",
          "Hinjewadi, Pune", "Baner, Pune", "Aundh, Pune", "Wakad, Pune"]


class MockBusinessProvider(BusinessSearchProvider):
    """Deterministic offline provider. Same query ⇒ same results."""

    name = "mock"

    def __init__(self, seed_base: int = 0) -> None:
        self._seed_base = seed_base

    def search_businesses(self, query: PlaceSearchQuery) -> list[PlaceDetails]:
        lower_loc = query.location_text.lower()
        if "timeout" in lower_loc or "timeout" in query.category.lower():
            raise ProviderTimeout("mock provider timeout requested")
        if "empty" in lower_loc:
            return []

        count = min(query.max_results, 12)
        results: list[PlaceDetails] = []
        for i in range(count):
            key = (i + self._seed_base) % len(_NAMES)
            name = f"{_NAMES[key]} {_SUFFIX.get(query.category.lower().strip(), query.category.title())}"
            area = _AREAS[i % len(_AREAS)]
            street = _STREETS[i % len(_STREETS)]
            has_website = i % 4 != 0  # every 4th business lacks a website
            results.append(PlaceDetails(
                provider=self.name,
                provider_place_id=f"mock_place_{query.category.lower().strip()}_{i + 1:03d}",
                business_name=name,
                category=query.category.title(),
                address=f"{10 + i} {street}, {area} 4110{i:02d}",
                phone=f"+9198765{i:05d}" if i % 7 != 3 else "",
                website=f"https://www.{_NAMES[key].lower()}{query.category.lower()}.example.com"
                if has_website else "",
                rating=round(3.5 + (i % 15) / 10.0, 1) if i % 5 else None,
                review_count=25 + i * 17 if i % 5 else None,
                latitude=18.5204 + i * 0.005,
                longitude=73.8567 + i * 0.005,
                maps_url=f"https://maps.google.com/?cid=mock{i}",
                opening_hours="Mon-Sun 9:00 AM - 9:00 PM" if i % 2 else "",
                source_timestamp=datetime.now(timezone.utc).isoformat(),
            ))
        return results

    def get_business_details(self, place_id: str, language: str = "en") -> Optional[PlaceDetails]:
        if "timeout" in place_id:
            raise ProviderTimeout("mock provider timeout requested")
        return PlaceDetails(
            provider=self.name,
            provider_place_id=place_id,
            business_name=f"Mock Business {place_id[-6:]}",
            category="Mock",
            address="1 Mock Street, Pune",
            phone="+919876543210",
            website="https://mock.example.com",
            rating=4.2,
            review_count=100,
            source_timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def health_check(self) -> bool:
        return True
