# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Tests for Geoapify Places API provider."""
import pytest
import httpx

from app.core.config import get_settings
from app.providers.base import (
    PlaceSearchQuery,
    ProviderAuthError,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.factory import get_provider, _provider_configured
from app.providers.geoapify import GeoapifyPlacesProvider
from app.schemas.search import SearchCreateRequest


_MOCK_FEATURE = {
    "type": "Feature",
    "properties": {
        "name": "Barista Lavazza",
        "formatted": "Barista Lavazza, Moledina Road, Pune - 411001, India",
        "categories": ["catering", "catering.cafe"],
        "lat": 18.519,
        "lon": 73.875,
        "place_id": "geo_place_12345",
        "contact": {
            "phone": "+91 20 12345678",
        },
        "website": "https://barista.co.in",
        "opening_hours": "09:00 - 22:00",
        "datasource": {"sourcename": "openstreetmap"},
    },
    "geometry": {
        "type": "Point",
        "coordinates": [73.875, 18.519],
    },
}


class TestGeoapifyProvider:
    def test_missing_api_key_raises_auth_error(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "geoapify_api_key", "")
        with pytest.raises(ProviderAuthError):
            GeoapifyPlacesProvider(api_key="")

    def test_factory_resolves_geoapify(self):
        provider = get_provider("geoapify")
        assert provider.name == "geoapify"
        assert isinstance(provider, GeoapifyPlacesProvider)

    def test_provider_configured_check(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "geoapify_api_key", "test_key")
        assert _provider_configured("geoapify") is True
        monkeypatch.setattr(get_settings(), "geoapify_api_key", "")
        assert _provider_configured("geoapify") is False

    def test_schema_validator_accepts_geoapify(self):
        req = SearchCreateRequest(
            free_text="coffee in pune",
            provider="geoapify",
        )
        assert req.provider == "geoapify"

    def test_feature_mapping(self):
        provider = GeoapifyPlacesProvider(api_key="dummy_test_key")
        place = provider._map_feature_to_place(_MOCK_FEATURE)

        assert place.provider == "geoapify"
        assert place.provider_place_id == "geo_place_12345"
        assert place.business_name == "Barista Lavazza"
        assert place.phone == "+91 20 12345678"
        assert place.website == "https://barista.co.in"
        assert place.latitude == 18.519
        assert place.longitude == 73.875
        assert "18.519,73.875" in place.maps_url

    def test_category_mapping(self):
        provider = GeoapifyPlacesProvider(api_key="dummy_test_key")
        assert provider._map_category("hospital")[0] == "healthcare.hospital"
        assert provider._map_category("cafe")[0] == "catering.cafe"
        assert provider._map_category("dentist")[0] == "healthcare.dentist"
        assert provider._map_category("hotel")[0] == "accommodation.hotel"

    def test_search_businesses_with_mock(self, monkeypatch):
        class MockResponse:
            status_code = 200

            def json(self):
                return {"features": [_MOCK_FEATURE]}

            def raise_for_status(self):
                pass

        provider = GeoapifyPlacesProvider(api_key="dummy_test_key")
        monkeypatch.setattr(provider, "_request", lambda method, url, params=None: MockResponse())

        query = PlaceSearchQuery(
            category="cafe",
            location_text="Pune",
            latitude=18.52,
            longitude=73.85,
            max_results=5,
        )
        results = provider.search_businesses(query)
        assert len(results) == 1
        assert results[0].business_name == "Barista Lavazza"

    def test_live_search_if_key_configured(self):
        settings = get_settings()
        if not settings.geoapify_api_key:
            pytest.skip("No GEOAPIFY_API_KEY set")

        provider = GeoapifyPlacesProvider()
        query = PlaceSearchQuery(
            category="cafe",
            location_text="Koregaon Park, Pune",
            latitude=18.5366,
            longitude=73.8932,
            radius_m=3000,
            max_results=3,
        )
        results = provider.search_businesses(query)
        assert len(results) > 0
        assert results[0].provider == "geoapify"
        assert results[0].business_name != ""
