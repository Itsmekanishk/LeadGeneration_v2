"""Tests for the open-source stack integration:
Nominatim (OpenStreetMap) provider + Disify disposable-email checks.

All network access is faked; the suite runs fully offline.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.base import PlaceSearchQuery  # noqa: E402
from app.providers.factory import get_provider  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    """Minimal httpx.Client stand-in returning queued payloads."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params,
                           "headers": headers})
        if not self.payloads:
            raise AssertionError("unexpected extra HTTP call")
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, FakeResponse):
            return item
        return FakeResponse(item)  # raw payload → wrap


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch):
    """Skip the 1 req/s Nominatim throttle inside tests."""
    import app.providers.nominatim as nom

    monkeypatch.setattr(nom, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(nom, "_LAST_REQUEST_TS", 0.0)


# ---------------------------------------------------------------------------
# Nominatim provider
# ---------------------------------------------------------------------------

_SEARCH_HIT = {
    "lat": "18.5204", "lon": "73.8567", "osm_type": "node", "osm_id": 42,
    "name": "Sunrise Hospital",
    "display_name": "Sunrise Hospital, Porwal Road, Pune, India",
    "address": {"house_number": "5", "road": "Porwal Road", "city": "Pune",
                "postcode": "411015", "country": "India"},
    "extratags": {"website": "https://sunrisehospital.in",
                  "phone": "+91 98220 12345"},
    "opening_hours": "24/7",
}


class TestNominatimProvider:
    def test_factory_resolves_nominatim(self):
        provider = get_provider("nominatim")
        assert provider.name == "nominatim"

    def test_search_maps_osm_hits_to_places(self, monkeypatch):
        import app.providers.nominatim as nom

        client = FakeClient([[_SEARCH_HIT]])
        provider = nom.NominatimProvider(client=client)
        results = provider.search_businesses(PlaceSearchQuery(
            category="hospital", location_text="Porwal Road, Pune",
            latitude=18.52, longitude=73.85, max_results=10))
        assert len(results) == 1
        place = results[0]
        assert place.provider == "nominatim"
        assert place.provider_place_id == "osm_node_42"
        assert place.business_name == "Sunrise Hospital"
        assert place.website == "https://sunrisehospital.in"
        assert place.phone == "+91 98220 12345"
        assert place.latitude == 18.5204
        assert place.maps_url == "https://www.openstreetmap.org/node/42"
        assert "google" not in place.maps_url

    def test_search_sends_ua_and_bounded_params(self, monkeypatch):
        import app.providers.nominatim as nom

        client = FakeClient([[]])
        provider = nom.NominatimProvider(client=client)
        provider.search_businesses(PlaceSearchQuery(
            category="hospital", location_text="Pune", max_results=5))
        params = client.calls[0]["params"]
        assert params["format"] == "jsonv2"
        assert int(params["limit"]) <= 50

    def test_unnamed_features_skipped(self, monkeypatch):
        import app.providers.nominatim as nom

        hit = dict(_SEARCH_HIT, name="")
        client = FakeClient([[hit]])
        provider = nom.NominatimProvider(client=client)
        results = provider.search_businesses(PlaceSearchQuery(
            category="hospital", location_text="Pune", max_results=5))
        assert results == []

    def test_http_429_maps_to_provider_unavailable(self, monkeypatch):
        import app.providers.nominatim as nom

        client = FakeClient([FakeResponse([], status_code=429)])
        provider = nom.NominatimProvider(client=client)
        with pytest.raises(nom.ProviderUnavailable):
            provider.search_businesses(PlaceSearchQuery(
                category="cafe", location_text="Pune", max_results=5))

    def test_health_check_false_on_failure(self, monkeypatch):
        import app.providers.nominatim as nom

        client = FakeClient([httpx.TimeoutException("boom")])
        provider = nom.NominatimProvider(client=client)
        assert provider.health_check() is False

    def test_resolve_location_returns_tuple(self, monkeypatch):
        import app.providers.nominatim as nom

        client = FakeClient([[_SEARCH_HIT]])
        real_cls = nom.NominatimProvider
        monkeypatch.setattr(
            nom, "NominatimProvider",
            lambda: real_cls(client=client))
        out = nom.resolve_location("Porwal Road, Pune", country="in")
        assert out is not None
        assert out[0] == 18.5204 and out[1] == 73.8567
        assert "Pune" in out[2]

    def test_resolve_location_none_on_empty(self):
        import app.providers.nominatim as nom

        assert nom.resolve_location("") is None

    def test_zero_google_endpoints_in_provider(self):
        import app.providers.nominatim as nom

        src = Path(nom.__file__).read_text(encoding="utf-8")
        assert "maps.googleapis" not in src


# ---------------------------------------------------------------------------
# Disify disposable-email integration (contact_normalizer)
# ---------------------------------------------------------------------------

class TestDisifyIntegration:
    def test_disposable_email_downgraded_to_low(self, monkeypatch):
        from app.services.contact_normalizer import extract_emails_from_text

        monkeypatch.setattr(
            "app.services.contact_normalizer._disify_disposable",
            lambda email: True)
        findings = extract_emails_from_text(
            "contact trash@mailinator.com",
            source_url="https://sunrisehospital.in",
            business_domain="sunrisehospital.in",
            check_disposable=True)
        assert findings[0].confidence == "LOW"
        assert findings[0].disposable is True

    def test_non_disposable_keeps_confidence(self, monkeypatch):
        from app.services.contact_normalizer import extract_emails_from_text

        monkeypatch.setattr(
            "app.services.contact_normalizer._disify_disposable",
            lambda email: False)
        findings = extract_emails_from_text(
            "contact staff@gmail.com",
            source_url="https://sunrisehospital.in",
            business_domain="sunrisehospital.in",
            check_disposable=True)
        assert findings[0].confidence == "MEDIUM"  # free host on official site
        assert findings[0].disposable is False

    def test_high_confidence_skips_disify_call(self, monkeypatch):
        from app.services import contact_normalizer as cn

        calls = []

        def fake_disify(email):
            calls.append(email)
            return False

        monkeypatch.setattr(cn, "_disify_disposable", fake_disify)
        findings = cn.extract_emails_from_text(
            "write to info@sunrisehospital.in",
            source_url="https://sunrisehospital.in",
            business_domain="sunrisehospital.in",
            check_disposable=True)
        assert findings[0].confidence == "HIGH"
        assert calls == []  # no API call needed for own-domain emails

    def test_disify_unreachable_keeps_tier(self, monkeypatch):
        from app.services.contact_normalizer import extract_emails_from_text

        monkeypatch.setattr(
            "app.services.contact_normalizer._disify_disposable",
            lambda email: None)  # unreachable
        findings = extract_emails_from_text(
            "contact staff@gmail.com",
            source_url="https://sunrisehospital.in",
            business_domain="sunrisehospital.in",
            check_disposable=True)
        assert findings[0].confidence == "MEDIUM"  # unchanged
        assert findings[0].disposable is None

    def test_disify_check_off_by_default(self):
        from app.services.contact_normalizer import extract_emails_from_text

        findings = extract_emails_from_text(
            "contact trash@mailinator.com",
            source_url="https://sunrisehospital.in",
            business_domain="sunrisehospital.in")  # check_disposable defaults False
        # mailinator is not in the local free-host list; without the Disify
        # verdict the address keeps its unknown-host tier (LOW).
        assert findings[0].confidence == "LOW"
        assert findings[0].disposable is None
