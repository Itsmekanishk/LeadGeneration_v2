# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Tests for automated OSINT web presence and contact resolution."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.crawlers.contact_finder import crawl_business_website
from app.services.contact_normalizer import extract_phones_from_text, normalize_phone
from app.services.osint_finder import (
    OsintResult,
    _clean_slug,
    _probe_domains,
    resolve_business_web_presence,
)


def test_ignored_hosts_match_on_host_boundaries():
    """Regression: substring matching made ``x.com`` swallow unrelated domains."""
    from app.services.osint_finder import _IGNORED_HOSTS
    from app.utils.normalization import host_matches

    assert host_matches("x.com", _IGNORED_HOSTS)
    assert host_matches("m.x.com", _IGNORED_HOSTS)
    assert host_matches("practo.com", _IGNORED_HOSTS)
    # ...but not domains that merely contain an ignored name as a substring.
    assert not host_matches("matrix.com", _IGNORED_HOSTS)
    assert not host_matches("phoenix.com", _IGNORED_HOSTS)
    assert not host_matches("linux.com", _IGNORED_HOSTS)


def test_snippet_attribution_requires_business_mention():
    """A phone in an unrelated result snippet must never become the lead's phone."""
    from app.services.osint_finder import _attributable_snippet_text

    snippets = [
        "Unrelated shop in Delhi, call 011 1234 5678",
        "Sunrise Hospital, Pune — phone 020 2665 4321",
    ]
    text = _attributable_snippet_text(snippets, "Sunrise Hospital", "Pune")
    assert "020 2665 4321" in text
    assert "011 1234 5678" not in text
    # Nothing attributable → refuse to guess rather than take a wrong number.
    assert _attributable_snippet_text(
        ["nothing here", "another result"], "Sunrise Hospital", "Pune"
    ) == ""


def test_clean_slug():
    slugs = _clean_slug("Chafa Cafe & Studio")
    assert "chafastudio" in slugs or "chafacafeandstudio" in slugs or "chafacafe" in slugs

    slugs_empty = _clean_slug("   ")
    assert slugs_empty == []


def test_probe_domains_mocked():
    with patch("socket.getaddrinfo") as mock_dns, patch("httpx.Client") as mock_http:
        mock_dns.return_value = True
        client_instance = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://chafacafeandstudio.in"
        client_instance.head.return_value = mock_resp
        mock_http.return_value.__enter__.return_value = client_instance

        found = _probe_domains("Chafa Cafe & Studio", country="in")
        assert found == "https://chafacafeandstudio.in"


def test_whatsapp_regex_and_normalizer():
    text = (
        'Chat with us on WhatsApp: <a href="https://wa.me/919876543210">WhatsApp</a> '
        'or call +91-98765-43211 or api.whatsapp.com/send?phone=919876543212'
    )
    phones = extract_phones_from_text(text)
    # Digits normalized
    assert any("9876543210" in p.replace("-", "") for p in phones)
    assert any("9876543211" in p.replace("-", "") for p in phones)
    assert any("9876543212" in p.replace("-", "") for p in phones)


def test_resolve_business_web_presence_cached():
    with patch("app.services.osint_finder._search_web_yahoo", return_value=OsintResult(website="https://testbiz.in", source="yahoo_web")), \
         patch("app.services.osint_finder._probe_domains", return_value=None), \
         patch("app.services.osint_finder._search_nominatim_extratags", return_value=None):
        res1 = resolve_business_web_presence("Unique Biz Test 12345", "Pune", "in")
        assert res1.website == "https://testbiz.in"

        # Second call should come from cache
        res2 = resolve_business_web_presence("Unique Biz Test 12345", "Pune", "in")
        assert res2.website == "https://testbiz.in"
        assert res2.source == "cache"


def test_crawl_business_website_extracts_whatsapp_and_tel():
    fake_html = """
    <html>
      <body>
        <h1>Welcome to Coffee Hub</h1>
        <p>Email: contact@coffeehub.in</p>
        <a href="https://wa.me/919822012345">Message on WhatsApp</a>
        <a href="tel:+912025678901">Call us</a>
      </body>
    </html>
    """
    with patch("app.crawlers.contact_finder.fetch_page") as mock_fetch, \
         patch("app.crawlers.contact_finder.is_allowed_by_robots", return_value=True):
        mock_page = MagicMock()
        mock_page.ok = True
        mock_page.html = fake_html
        mock_page.final_url = "https://coffeehub.in"
        mock_page.status = 200
        mock_page.status_kind = "ok"
        mock_page.duration_ms = 100
        mock_fetch.return_value = mock_page

        outcome = crawl_business_website("https://coffeehub.in", max_pages=1)
        assert outcome.ok is True
        assert any(e.email == "contact@coffeehub.in" for e in outcome.emails)
        assert any("9822012345" in p for p in outcome.phones)


def test_pipeline_osint_stage_enriches_business(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/pipe_osint.db")
    monkeypatch.setenv("PDF_STORAGE_PATH", str(tmp_path / "reports"))
    monkeypatch.setenv("ENABLE_WEBSITE_CRAWLING", "false")
    monkeypatch.setenv("ENABLE_OSINT_RESOLUTION", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.models import database as db_mod
    from app.models.base import Base
    from app.models.database import session_scope
    from app.models.models import Business, SearchJob, User
    from app.workers.pipeline import run_pipeline

    db_mod.reset_engine()
    Base.metadata.create_all(db_mod.get_engine())

    with session_scope() as db:
        user = User(email="osint_pipe@example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(
            user_id=user.id,
            query_raw="Cafes in Pune",
            parsed_category="Cafe",
            parsed_location="Pune",
            max_results=1,
            provider="nominatim",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    mock_osint = OsintResult(
        website="https://discovered-cafe.in",
        phones=["+919876500000"],
        emails=["hello@discovered-cafe.in"],
        source="osint",
    )
    with patch("app.workers.pipeline.search_with_fallback") as mock_search, \
         patch("app.workers.pipeline.resolve_business_web_presence", return_value=mock_osint):
        from app.providers.base import PlaceDetails

        mock_search.return_value = (
            [
                PlaceDetails(
                    provider="nominatim",
                    provider_place_id="osm_1",
                    business_name="Discovered Cafe",
                    category="cafe",
                    address="Pune",
                    phone="",
                    website="",
                )
            ],
            "nominatim",
            None,
        )

        with session_scope() as db:
            run_pipeline(db, job_id)

    with session_scope() as db:
        job = db.get(SearchJob, job_id)
        assert job.status == "completed"
        biz = db.query(Business).filter_by(job_id=job_id).first()
        assert biz is not None
        assert biz.website == "https://discovered-cafe.in"
        assert biz.phone == "+919876500000"
        assert biz.email == "hello@discovered-cafe.in"
        assert biz.phone_source == "osint"

