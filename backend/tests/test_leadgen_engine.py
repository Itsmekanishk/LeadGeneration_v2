"""Tests for the standalone leadgen engine (open-source master prompt).

Covers the four pillars: Email Confidence tiers, Unique Hash dedup,
Crawl Audit Trail (robots + SSRF), phone normalization (Numverify shape +
offline fallback), Nominatim/REST Countries geocoding and persistence.
All network access is mocked; the suite runs fully offline.
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(ENGINE_ROOT))


# ---------------------------------------------------------------------------
# Isolation: never touch the network or a real .env in tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    """Point the engine at a temp DB and strip every credential."""

    import leadgen.config as config_mod

    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("NUMVERIFY_API_KEY", raising=False)
    monkeypatch.delenv("APILAYER_API_KEY", raising=False)
    monkeypatch.setenv("LEADGEN_DB_PATH", str(tmp_path / "engine.db"))
    monkeypatch.setenv("LEADGEN_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("LEADGEN_REQUEST_DELAY_SECONDS", "0")
    config_mod.get_settings(refresh=True)
    yield config_mod.get_settings()
    config_mod.get_settings(refresh=True)  # restore default state


# ---------------------------------------------------------------------------
# Deduplication: Unique Hash = sha256(normalized_phone + domain)
# ---------------------------------------------------------------------------

class TestDedup:
    def test_same_phone_different_formatting_same_hash(self):
        from leadgen.dedup import compute_unique_hash

        # Numverify stores only international_format; "044 123 456" and
        # "+44 123 456" collapse to the same digits + domain.
        a = compute_unique_hash("+44 123 456", "example.co.uk")
        b = compute_unique_hash("44123456", "example.co.uk")
        assert a == b

    def test_first_unique_then_duplicate(self):
        from leadgen.dedup import DedupIndex

        idx = DedupIndex()
        first = idx.classify("+919822012345", "sunrisehospital.in")
        second = idx.classify("+91 98220 12345", "sunrisehospital.in")
        assert first.status == "UNIQUE" and first.first_seen
        assert second.status == "DUPLICATE" and not second.first_seen
        assert first.unique_hash == second.unique_hash

    def test_phoneless_leads_dedupe_by_domain(self):
        from leadgen.dedup import DedupIndex

        idx = DedupIndex()
        a = idx.classify("", "alpha.example")
        b = idx.classify("", "alpha.example")
        c = idx.classify("", "beta.example")
        assert a.status == "UNIQUE"
        assert b.status == "DUPLICATE"
        assert c.status == "UNIQUE"

    def test_phoneless_never_collides_with_phone_hash(self):
        from leadgen.dedup import compute_unique_hash

        no_phone = compute_unique_hash("", "example.com")
        with_phone = compute_unique_hash("919822012345", "example.com")
        assert no_phone != with_phone


# ---------------------------------------------------------------------------
# Email Confidence tiers
# ---------------------------------------------------------------------------

class TestEmailConfidence:
    def test_high_when_domain_matches_website(self):
        from leadgen.confidence import calculate_email_confidence

        assert calculate_email_confidence(
            "info@sunrisehospital.in", "https://sunrisehospital.in",
            is_disposable=False, found_on_website=True) == "HIGH"

    def test_high_for_subdomain_match(self):
        from leadgen.confidence import calculate_email_confidence

        assert calculate_email_confidence(
            "care@mail.sunrisehospital.in", "https://sunrisehospital.in",
            is_disposable=False, found_on_website=True) == "HIGH"

    def test_medium_free_host_found_on_site(self):
        from leadgen.confidence import calculate_email_confidence

        assert calculate_email_confidence(
            "sunrisehospital@gmail.com", "https://sunrisehospital.in",
            is_disposable=False, found_on_website=True) == "MEDIUM"

    def test_low_when_found_on_external_page(self):
        from leadgen.confidence import calculate_email_confidence

        assert calculate_email_confidence(
            "sunrisehospital@gmail.com", "https://sunrisehospital.in",
            is_disposable=False, found_on_website=False) == "LOW"

    def test_low_for_disposable_even_on_site(self):
        from leadgen.confidence import calculate_email_confidence

        assert calculate_email_confidence(
            "trash@mailinator.com", "https://sunrisehospital.in",
            is_disposable=True, found_on_website=True) == "LOW"

    def test_master_prompt_snippet_bug_is_fixed(self):
        """The original snippet made LOW unreachable for non-disposable
        emails; every non-HIGH case must still be able to land in LOW."""
        from leadgen.confidence import calculate_email_confidence

        # unknown host, external page ⇒ LOW (not MEDIUM)
        assert calculate_email_confidence(
            "x@some-random-host.net", "https://sunrisehospital.in",
            is_disposable=False, found_on_website=False) == "LOW"

    def test_broken_input_is_low(self):
        from leadgen.confidence import calculate_email_confidence

        assert calculate_email_confidence(
            "not-an-email", "https://sunrisehospital.in",
            is_disposable=False, found_on_website=True) == "LOW"


# ---------------------------------------------------------------------------
# Crawl Audit Trail: robots.txt + SSRF guard
# ---------------------------------------------------------------------------

class TestSSRFGuard:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8080/admin",
        "http://10.0.0.5/internal",
        "http://192.168.1.10/router",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://localhost:9000/",
        "file:///etc/passwd",
    ])
    def test_private_targets_blocked(self, url):
        from leadgen import audit

        result = audit.http_get(url, api_exempt=True)
        assert result.ok is False
        assert result.status_kind == "ssrf_blocked"
        assert result.audit["http_status"] is None
        assert "response_time_ms" in result.audit

    def test_dns_name_resolving_private_is_blocked(self, monkeypatch):
        from leadgen import audit

        monkeypatch.setattr(audit, "_resolve_ip", lambda host: "10.1.2.3")
        result = audit.http_get("https://internal.example/page")
        assert result.status_kind == "ssrf_blocked"

    def test_audit_record_shape(self):
        from leadgen import audit

        result = audit.http_get("http://127.0.0.1/x")
        rec = audit.audit_record(result, "test")
        assert rec["service"] == "test"
        assert rec["robots_allowed"] in (True, False)
        assert isinstance(rec["response_time_ms"], int)


class TestRobots:
    def test_disallowed_url_is_recorded_not_fetched(self, monkeypatch):
        from leadgen import audit

        monkeypatch.setattr(audit, "robots_allowed", lambda url, ua=None: False)
        result = audit.http_get("https://example.com/private-page")
        assert result.status_kind == "robots_disallowed"
        assert result.ok is False
        assert result.audit["robots_allowed"] is False
        assert result.audit["http_status"] is None

    def test_api_hosts_are_exempt_but_still_audited(self, engine_env, monkeypatch):
        from leadgen import audit

        class FakeResp:
            status_code = 200
            text = "{\"format\": true}"
            headers = {"content-type": "application/json"}

            def json(self):
                return {"format": True}

        monkeypatch.setattr(audit.requests, "get",
                            lambda *a, **kw: FakeResp())
        result = audit.http_get("https://disify.com/api/email/x@y.com")
        assert result.ok is True
        assert result.audit.get("api_exempt") is True
        assert result.audit["robots_allowed"] is True


# ---------------------------------------------------------------------------
# Numverify: international_format only, offline fallback
# ---------------------------------------------------------------------------

class TestNumverify:
    def test_uses_international_format_from_numverify(self, engine_env, monkeypatch):
        import leadgen.numverify as nv

        settings = engine_env
        monkeypatch.setattr(settings, "numverify_api_key", "test-key",
                            raising=False)

        class FakeResult:
            ok = True
            status = 200
            url = "http://apilayer.net/api/validate"
            robots_allowed = True
            json = {"valid": True, "number": "919822012345",
                    "international_format": "+91 98220 12345",
                    "country_code": "IN", "country_prefix": "91"}

        calls = {}

        def fake_get(url, **kwargs):
            calls["url"] = url
            calls["params"] = kwargs.get("params")
            return FakeResult()

        monkeypatch.setattr(nv.audit, "http_get", fake_get)
        out = nv.normalize_phone("+91 98220 12345")
        assert out.international_format == "+91 98220 12345"
        assert out.source == "numverify"
        assert out.valid is True
        assert calls["params"]["access_key"] == "test-key"
        assert out.audit["service"] == "numverify"

    def test_falls_back_without_key(self, engine_env):
        from leadgen.numverify import normalize_phone

        out = normalize_phone("098220 12345")  # Indian mobile
        assert out.international_format.startswith("+91")
        assert out.source in ("phonenumbers", "regex")
        assert out.audit["status_kind"] == "skipped_no_key"

    def test_garbage_phone_stays_invalid(self, engine_env):
        from leadgen.numverify import normalize_phone

        out = normalize_phone("not-a-phone")
        assert out.international_format == ""
        assert out.valid is False


# ---------------------------------------------------------------------------
# Geocoding: Nominatim primary, REST Countries fallback, zero Google
# ---------------------------------------------------------------------------

class TestGeocode:
    def test_nominatim_name_plus_city(self, engine_env, monkeypatch):
        import leadgen.geocode as geo

        class FakeResult:
            ok = True
            status = 200
            url = "https://nominatim.openstreetmap.org/search"
            robots_allowed = True
            json = [{
                "lat": "18.5204", "lon": "73.8567",
                "display_name": "Sunrise Hospital, Pune, India",
                "address": {"country": "India", "state": "Maharashtra",
                            "city": "Pune"},
            }]

        queries = []

        def fake_get(url, **kwargs):
            queries.append(kwargs.get("params", {}).get("q"))
            return FakeResult()

        monkeypatch.setattr(geo.audit, "http_get", fake_get)
        out = geo.geocode("Sunrise Hospital", "Pune", "in")
        assert out.found and out.source == "nominatim"
        assert out.latitude == 18.5204 and out.longitude == 73.8567
        assert queries[0] == "Sunrise Hospital, Pune"

    def test_open_meteo_fallback_when_nominatim_misses(self, engine_env, monkeypatch):
        import leadgen.geocode as geo

        def fake_get(url, **kwargs):
            class FakeResult:
                ok = True
                status = 200
                robots_allowed = True
            if "nominatim" in url:
                r = FakeResult()
                r.url, r.json = url, []  # no Nominatim hits at all
                return r
            r = FakeResult()
            r.json = {"results": [{"name": "Pune", "latitude": 18.5196,
                                   "longitude": 73.8553, "country": "India",
                                   "admin1": "Maharashtra"}]}
            r.url = url
            return r

        monkeypatch.setattr(geo.audit, "http_get", fake_get)
        out = geo.geocode("Unknown Rural Clinic", "Pune", "in")
        assert out.found
        assert out.source == "open-meteo"
        assert out.latitude == 18.5196

    def test_no_query_returns_unfound(self, engine_env):
        from leadgen.geocode import geocode

        out = geocode("", "")
        assert not out.found and out.source == ""

    def test_google_never_called(self):
        """The whole engine must be free of maps.googleapis.com endpoints."""
        import leadgen

        engine_dir = Path(leadgen.__file__).parent
        for py in engine_dir.glob("*.py"):
            assert "maps.googleapis" not in py.read_text(encoding="utf-8"), \
                f"Google Maps endpoint found in {py.name}"


# ---------------------------------------------------------------------------
# Disify
# ---------------------------------------------------------------------------

class TestDisify:
    def test_disposable_flag_from_api(self, engine_env, monkeypatch):
        import leadgen.disify as dis

        class FakeResult:
            ok = True
            status = 200
            url = "https://disify.com/api/email/someone@mailinator.com"
            robots_allowed = True
            json = {"format": True, "domain": "mailinator.com",
                    "disposable": True, "dns": True}

        monkeypatch.setattr(dis.audit, "http_get",
                            lambda url, **kw: FakeResult())
        out = dis.check_email("someone@mailinator.com")
        assert out.disposable is True and out.source == "disify"

    def test_free_host_detection(self, engine_env):
        from leadgen.disify import check_email

        # gmail is not "disposable" but is a free host (MEDIUM tier input)
        out = check_email("someone@gmail.com")
        assert out.is_free_host is True
        assert out.disposable is not True  # Disify unreachable in tests → None

    def test_unreachable_api_degrades_gracefully(self, engine_env, monkeypatch):
        import leadgen.disify as dis

        class FakeResult:
            ok = False
            status = None
            json = None
            url = "https://disify.com/api/email/someone@gmail.com"
            robots_allowed = True
            error = "connection refused"

        monkeypatch.setattr(dis.audit, "http_get",
                            lambda url, **kw: FakeResult())
        out = dis.check_email("someone@gmail.com")
        assert out.source == "unreachable"
        assert out.disposable is None  # unknown — never an exception


# ---------------------------------------------------------------------------
# SiteIntel: actor parsing + fallback extraction
# ---------------------------------------------------------------------------

class TestSiteIntel:
    def test_fallback_extracts_metadata_emails_socials(self, engine_env, monkeypatch):
        import leadgen.siteintel as si

        html = """
        <html><head><title>Sunrise Hospital</title>
        <meta name="description" content="Multi-speciality care in Pune">
        </head><body>
        <a href="mailto:info@sunrisehospital.in">Mail</a>
        <a href="https://facebook.com/sunrisehospital">FB</a>
        Write to care@sunrisehospital.in or call us.
        </body></html>
        """

        class FakeResult:
            ok = True
            status = 200
            content = html
            json = None
            url = "https://sunrisehospital.in"
            robots_allowed = True
            response_time_ms = 42

        monkeypatch.setattr(si.audit, "http_get",
                            lambda url, **kw: FakeResult())
        report = si.gather_site_intel("https://sunrisehospital.in")
        assert report.source == "fallback"
        assert report.title == "Sunrise Hospital"
        assert "info@sunrisehospital.in" in report.emails
        assert "care@sunrisehospital.in" in report.emails
        assert report.social_links == ["https://facebook.com/sunrisehospital"]
        assert report.audit and report.audit[0]["service"] == "siteintel"

    def test_actor_path_maps_fields(self, engine_env, monkeypatch):
        import leadgen.siteintel as si

        monkeypatch.setattr(engine_env, "apify_token", "test-token",
                            raising=False)

        class FakeResult:
            ok = True
            status = 200
            url = "https://api.apify.com/v2/acts/x/run-sync-get-dataset-items"
            robots_allowed = True
            content = ""
            response_time_ms = 15
            json = [{"domain": "sunrisehospital.in",
                     "title": "Sunrise Hospital",
                     "description": "Care",
                     "emails": ["info@sunrisehospital.in"],
                     "socialLinks": ["https://x.com/sunrisehospital"]}]

        monkeypatch.setattr(si.audit, "http_get",
                            lambda url, **kw: FakeResult())
        report = si.gather_site_intel("https://sunrisehospital.in")
        assert report.source == "actor"
        assert report.emails == ["info@sunrisehospital.in"]
        assert report.social_links == ["https://x.com/sunrisehospital"]

    def test_dead_site_degrades_to_empty_report(self, engine_env, monkeypatch):
        import leadgen.siteintel as si

        class FakeResult:
            ok = False
            status = None
            content = ""
            json = None
            url = "https://dead.example"
            robots_allowed = True
            error = "timeout"

        monkeypatch.setattr(si.audit, "http_get",
                            lambda url, **kw: FakeResult())
        report = si.gather_site_intel("https://dead.example")
        assert report.source == "fallback"
        assert report.emails == []
        assert report.audit[0]["status_kind"] in ("http_error", "error")


# ---------------------------------------------------------------------------
# Pipeline + persistence + exports (end-to-end, offline)
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_full_lead_flow(self, engine_env, monkeypatch):
        import leadgen.pipeline as pipe

        class FakeIntel:
            title = "Sunrise Hospital"
            description = "Care"
            emails = ["info@sunrisehospital.in"]
            social_links = []
            source = "fallback"
            audit = [{"service": "siteintel", "url": "x", "http_status": 200,
                      "response_time_ms": 120, "robots_allowed": True,
                      "status_kind": "ok", "error": ""}]

        monkeypatch.setattr(pipe, "gather_site_intel",
                            lambda website: FakeIntel())

        class FakeDisify:
            disposable = False
            is_free_host = False
            source = "disify"
            audit = {"service": "disify", "url": "u", "http_status": 200,
                     "response_time_ms": 60, "robots_allowed": True,
                     "status_kind": "ok", "error": ""}
            audit = {"service": "disify", "url": "u", "http_status": 200,
                     "response_time_ms": 60, "robots_allowed": True,
                     "status_kind": "ok", "error": ""}

        monkeypatch.setattr(pipe, "check_email", lambda email: FakeDisify())
        monkeypatch.setattr(
            pipe, "normalize_phone",
            lambda raw, **kw: type("P", (), {
                "international_format": "+91 98220 12345",
                "country_code": "+91", "line_type": "mobile",
                "valid": True, "source": "numverify",
                "audit": {"service": "numverify", "url": "u",
                          "http_status": 200, "response_time_ms": 90,
                          "robots_allowed": True, "status_kind": "ok",
                          "error": ""}})())
        monkeypatch.setattr(
            pipe, "geocode",
            lambda name, city, cc: type("G", (), {
                "latitude": 18.52, "longitude": 73.85,
                "display_name": "Pune", "source": "nominatim",
                "audit": [], "raw": {}, "found": True})())

        from leadgen.dedup import DedupIndex
        from leadgen.pipeline import LeadInput

        item = LeadInput(
            business_name="Sunrise Hospital",
            website="https://sunrisehospital.in",
            city="Pune", country_code="in", phone="098220 12345")
        result = pipe.process_lead(item, DedupIndex())
        rec = result.record
        assert rec["email"] == "info@sunrisehospital.in"
        assert rec["email_confidence"] == "HIGH"
        assert rec["phone_normalized"] == "+91 98220 12345"
        assert rec["dedup_status"] == "UNIQUE"
        assert rec["latitude"] == 18.52 and rec["geo_source"] == "nominatim"
        assert rec["crawl_http_status"] == 200
        assert rec["crawl_robots_allowed"] is True
        assert any(a["service"] == "siteintel" for a in rec["audit_trail"])

    def test_duplicate_lead_flagged_and_persisted(self, engine_env, monkeypatch):
        import leadgen.pipeline as pipe

        class FakeIntel:
            title = ""
            description = ""
            emails = []
            social_links = []
            source = "fallback"
            audit = [{"service": "siteintel", "url": "x", "http_status": 200,
                      "response_time_ms": 50, "robots_allowed": True,
                      "status_kind": "ok", "error": ""}]

        monkeypatch.setattr(pipe, "gather_site_intel",
                            lambda website: FakeIntel())
        monkeypatch.setattr(
            pipe, "check_email",
            lambda email: type("D", (), {
                "disposable": False, "is_free_host": False,
                "source": "disify",
                "audit": {"service": "disify", "url": "u",
                          "http_status": 200, "response_time_ms": 60,
                          "robots_allowed": True, "status_kind": "ok",
                          "error": ""}})())
        monkeypatch.setattr(
            pipe, "normalize_phone",
            lambda raw, **kw: type("P", (), {
                "international_format": "+91 98220 12345",
                "country_code": "+91", "line_type": "", "valid": True,
                "source": "regex",
                "audit": {"service": "numverify", "url": "", "http_status": None,
                          "response_time_ms": 0, "robots_allowed": True,
                          "status_kind": "skipped_no_key",
                          "error": "", "source": "regex"}})())
        monkeypatch.setattr(
            pipe, "geocode",
            lambda name, city, cc: type("G", (), {
                "latitude": None, "longitude": None, "display_name": "",
                "source": "", "audit": [], "raw": {}, "found": False})())

        from leadgen.dedup import DedupIndex
        from leadgen.pipeline import LeadInput

        dedup = DedupIndex()
        a = pipe.process_lead(
            LeadInput(business_name="A", website="https://a.example",
                      phone="+919822012345"), dedup)
        b = pipe.process_lead(
            LeadInput(business_name="B", website="https://a.example",
                      phone="098220 12345"), dedup)
        assert a.record["dedup_status"] == "UNIQUE"
        assert b.record["dedup_status"] == "DUPLICATE"
        assert a.record["unique_hash"] == b.record["unique_hash"]

    def test_persistence_and_export(self, engine_env, monkeypatch, tmp_path):
        import leadgen.exports as exports
        import leadgen.pipeline as pipe
        import leadgen.store as store

        class FakeIntel:
            title = "T"
            description = ""
            emails = ["info@sunrisehospital.in"]
            social_links = []
            source = "fallback"
            audit = [{"service": "siteintel", "url": "x", "http_status": 200,
                      "response_time_ms": 10, "robots_allowed": True,
                      "status_kind": "ok", "error": ""}]

        monkeypatch.setattr(pipe, "gather_site_intel",
                            lambda website: FakeIntel())
        monkeypatch.setattr(
            pipe, "check_email",
            lambda email: type("D", (), {
                "disposable": False, "is_free_host": False,
                "source": "disify",
                "audit": {"service": "disify", "url": "u",
                          "http_status": 200, "response_time_ms": 60,
                          "robots_allowed": True, "status_kind": "ok",
                          "error": ""}})())
        monkeypatch.setattr(
            pipe, "normalize_phone",
            lambda raw, **kw: type("P", (), {
                "international_format": "+91 98220 12345",
                "country_code": "+91", "line_type": "", "valid": True,
                "source": "regex",
                "audit": {"service": "numverify", "url": "", "http_status": None,
                          "response_time_ms": 0, "robots_allowed": True,
                          "status_kind": "skipped_no_key",
                          "error": "", "source": "regex"}})())
        monkeypatch.setattr(
            pipe, "geocode",
            lambda name, city, cc: type("G", (), {
                "latitude": 18.52, "longitude": 73.85,
                "display_name": "Pune", "source": "nominatim", "audit": [],
                "raw": {}, "found": True})())

        from leadgen.dedup import DedupIndex
        from leadgen.pipeline import LeadInput

        conn = store.connect(str(tmp_path / "p.db"))
        try:
            pipe.process_lead(
                LeadInput(business_name="Sunrise Hospital",
                          website="https://sunrisehospital.in", city="Pune",
                          country_code="in", phone="098220 12345"),
                DedupIndex(), conn)
            leads = store.all_leads(conn)
            assert len(leads) == 1
            row = leads[0]
            assert row["email_confidence"] == "HIGH"
            assert row["crawl_response_time_ms"] == 10
            assert row["crawl_robots_allowed"] == 1

            events = conn.execute(
                "SELECT * FROM crawl_audit_events").fetchall()
            assert len(events) >= 1  # siteintel + numverify + geocode rows

            j_path, c_path = exports.export_all(conn=conn,
                                                output_dir=tmp_path / "out")
            data = json.loads(j_path.read_text(encoding="utf-8"))
            assert data[0]["business_name"] == "Sunrise Hospital"
            csv_text = c_path.read_text(encoding="utf-8")
            assert "Sunrise Hospital" in csv_text
        finally:
            conn.close()
