"""Provider layer tests."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.base import (  # noqa: E402
    PlaceSearchQuery,
    ProviderAuthError,
    ProviderTimeout,
)
from app.providers.factory import get_provider  # noqa: E402
from app.providers.mock_provider import MockBusinessProvider  # noqa: E402


def test_mock_provider_deterministic():
    p = MockBusinessProvider()
    q = PlaceSearchQuery(category="hospital", location_text="Porwal Road, Pune",
                         max_results=8)
    r1 = p.search_businesses(q)
    r2 = p.search_businesses(q)
    assert [x.provider_place_id for x in r1] == [x.provider_place_id for x in r2]
    assert len(r1) == 8
    assert all(x.provider == "mock" for x in r1)


def test_mock_provider_timeout_scenario():
    p = MockBusinessProvider()
    with pytest.raises(ProviderTimeout):
        p.search_businesses(PlaceSearchQuery(
            category="hospital", location_text="timeout town", max_results=5))


def test_mock_provider_empty_scenario():
    p = MockBusinessProvider()
    results = p.search_businesses(PlaceSearchQuery(
        category="hospital", location_text="empty lands", max_results=5))
    assert results == []


def test_mock_provider_normalizes_required_fields():
    p = MockBusinessProvider()
    results = p.search_businesses(PlaceSearchQuery(
        category="hospital", location_text="Pune", max_results=3))
    for place in results:
        assert place.provider_place_id
        assert place.business_name
        assert place.source_timestamp


def test_factory_resolves_mock():
    provider = get_provider("mock")
    assert provider.name == "mock"
    assert provider.health_check() is True


def test_factory_rejects_unknown():
    from app.providers.base import ProviderError

    with pytest.raises(ProviderError):
        get_provider("nope")


def test_google_provider_requires_api_key(monkeypatch):
    monkeypatch.setenv("BUSINESS_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setenv("PROVIDER_STRICT_CONFIG", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ProviderAuthError):
        get_provider("google")
    get_settings.cache_clear()
