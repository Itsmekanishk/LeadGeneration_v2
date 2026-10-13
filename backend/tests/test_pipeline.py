"""End-to-end pipeline test with the mock provider (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.base import Base  # noqa: E402
from app.models.database import get_engine, session_scope  # noqa: E402
from app.models.models import Business, BusinessContact, SearchJob, User  # noqa: E402
from app.services.export_service import generate_export  # noqa: E402
from app.workers.pipeline import _apply_scan_results, run_pipeline  # noqa: E402


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/pipe.db")
    monkeypatch.setenv("PDF_STORAGE_PATH", str(tmp_path / "reports"))
    monkeypatch.setenv("BUSINESS_PROVIDER", "mock")
    monkeypatch.setenv("ENABLE_WEBSITE_CRAWLING", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.models import database as db_mod

    db_mod.reset_engine()
    Base.metadata.create_all(db_mod.get_engine())
    return db_mod


def test_full_pipeline_completes(monkeypatch, tmp_path):
    db_mod = _setup(monkeypatch, tmp_path)

    with session_scope() as db:
        user = User(email="pipe@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Hospitals in Porwal Road Pune",
                        parsed_category="Hospitals", parsed_location="Porwal Road, Pune",
                        max_results=12, provider="mock")
        db.add(job)
        db.commit()
        job_id = job.id
        user_id = user.id

    with session_scope() as db:
        run_pipeline(db, job_id)

    with session_scope() as db:
        job = db.get(SearchJob, job_id)
        assert job.status == "completed"
        assert job.total == 12
        assert job.completed == 12
        businesses = db.query(Business).filter_by(job_id=job_id).all()
        assert len(businesses) == 12
        # phones normalized by the provider path
        assert sum(1 for b in businesses if b.phone_normalized) > 0
        # every business keeps provenance
        assert all(b.source for b in businesses)

        # exports work on the completed job
        reports_dir = tmp_path / "reports"
        for fmt in ("pdf", "csv", "xlsx"):
            filename, rows = generate_export(db, job, fmt, user_id)
            assert filename.startswith("report_") and rows == 12
            path = reports_dir / filename
            assert path.exists()


def test_contact_persistence_merges_phone_only_and_email_rows(monkeypatch, tmp_path):
    """Website contacts merge into existing rows, never duplicating them.

    Regression: business_contacts keyed contacts on (business_id, email) while
    email defaults to "", so a lead with a provider phone and a website phone
    inserted two empty-email rows and crashed the job with an IntegrityError.
    Contacts now key on (business_id, email, phone_normalized): same address →
    upgrade confidence in place, distinct number → its own row.
    """
    db_mod = _setup(monkeypatch, tmp_path)

    with session_scope() as db:
        user = User(email="merge@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Clinics in Pune",
                        parsed_category="Clinic", parsed_location="Pune",
                        max_results=5, provider="mock")
        db.add(job)
        db.flush()
        biz = Business(job_id=job.id, provider="mock", provider_place_id="merge_1",
                       name="Merge Clinic", category="Clinic",
                       phone="+919876543210", phone_normalized="+919876543210",
                       phone_source="provider",
                       website="https://mergeclinic.example.com")
        db.add(biz)
        db.flush()
        db.add(BusinessContact(business_id=biz.id, phone="+919876543210",
                               phone_normalized="+919876543210", source="provider",
                               confidence="HIGH"))
        db.commit()
        job_id, biz_id = job.id, biz.id

    scan = {
        "pages": [{"url": "https://mergeclinic.example.com/contact",
                   "page_type": "contact", "status": "ok", "http_status": 200,
                   "emails": 1, "phones": 2, "duration_ms": 10}],
        "emails": [
            {"email": "info@mergeclinic.example.com",
             "source_url": "https://mergeclinic.example.com/contact",
             "confidence": "MEDIUM", "context": "", "disposable": None},
            {"email": "info@mergeclinic.example.com",
             "source_url": "https://mergeclinic.example.com/about",
             "confidence": "HIGH", "context": "", "disposable": False},
        ],
        "phones": [
            {"raw": "+91 98220 12345", "confidence": "MEDIUM",
             "source_url": "https://mergeclinic.example.com/contact"},
            {"raw": "+91 98220 67890", "confidence": "MEDIUM",
             "source_url": "https://mergeclinic.example.com/contact"},
        ],
        "ok": True,
        "summary": "1/1 pages ok, 1 emails, 2 phones",
    }
    with session_scope() as db:
        biz = db.get(Business, biz_id)
        _apply_scan_results(db, biz, scan)  # must not raise IntegrityError
        db.commit()

    with session_scope() as db:
        contacts = db.query(BusinessContact).filter_by(business_id=biz_id).all()
        emails = [c.email for c in contacts if c.email]
        assert emails.count("info@mergeclinic.example.com") == 1
        merged = next(c for c in contacts if c.email == "info@mergeclinic.example.com")
        assert merged.confidence == "HIGH"       # upgraded from the MEDIUM hit
        assert merged.email_disposable is False  # verdict recorded on merge
        phone_rows = [c for c in contacts if c.phone and not c.email]
        # Every distinct number is kept: the provider's line plus both website
        # finds (reception + appointments is the whole point of keeping them).
        assert sorted(c.phone_normalized for c in phone_rows) == [
            "+919822012345", "+919822067890", "+919876543210",
        ]
        provider_row = next(c for c in phone_rows
                            if c.phone_normalized == "+919876543210")
        assert provider_row.source == "provider"
        assert sum(1 for c in phone_rows if c.source == "website") == 2
        biz = db.get(Business, biz_id)
        assert biz.email == "info@mergeclinic.example.com"
        assert biz.email_confidence == "HIGH"


def test_later_scan_adds_new_numbers_without_repeating(monkeypatch, tmp_path):
    """A second scan stores the numbers it hasn't seen and never repeats one."""
    db_mod = _setup(monkeypatch, tmp_path)

    with session_scope() as db:
        user = User(email="repeat@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Clinics in Pune",
                        parsed_category="Clinic", parsed_location="Pune",
                        max_results=5, provider="mock")
        db.add(job)
        db.flush()
        biz = Business(job_id=job.id, provider="mock", provider_place_id="repeat_1",
                       name="Repeat Clinic", category="Clinic",
                       phone="+919876500000", phone_normalized="+919876500000",
                       phone_source="provider")
        db.add(biz)
        db.flush()
        db.add(BusinessContact(business_id=biz.id, phone="+919876500000",
                               phone_normalized="+919876500000", source="provider",
                               confidence="HIGH"))
        db.commit()
        biz_id = biz.id

    def scan(page_url: str, phones: list[str]) -> dict:
        return {
            "pages": [{"url": page_url, "page_type": "contact", "status": "ok",
                       "http_status": 200, "emails": 0, "phones": len(phones),
                       "duration_ms": 5}],
            "emails": [],
            "phones": [{"raw": p, "confidence": "MEDIUM", "source_url": page_url}
                       for p in phones],
            "ok": True,
            "summary": "scan",
        }

    first, second = "+91 98220 11111", "+91 98220 22222"
    with session_scope() as db:
        _apply_scan_results(db, db.get(Business, biz_id),
                            scan("https://repeat.example.com/contact", [first, second]))
        db.commit()
    # a later page repeats one number and adds another
    with session_scope() as db:
        _apply_scan_results(db, db.get(Business, biz_id),
                            scan("https://repeat.example.com/about", [second, "+91 98220 33333"]))
        db.commit()

    with session_scope() as db:
        numbers = sorted(
            c.phone_normalized for c in
            db.query(BusinessContact).filter_by(business_id=biz_id).all()
            if c.phone
        )
    assert numbers == [
        "+919822011111", "+919822022222", "+919822033333", "+919876500000",
    ], "each distinct number stored exactly once"


def test_scan_business_website_forwards_country(monkeypatch):
    """The job's country must reach the crawler so non-India numbers normalize."""
    import app.workers.pipeline as pipeline_mod
    from app.crawlers.contact_finder import CrawlOutcome

    captured = {}

    def fake_crawl(start_url, business_domain="", max_pages=None, default_country="IN"):
        captured.update(start_url=start_url, business_domain=business_domain,
                        default_country=default_country)
        return CrawlOutcome(start_url=start_url)

    monkeypatch.setattr(pipeline_mod, "crawl_business_website", fake_crawl)

    scan = pipeline_mod._scan_business_website(
        "biz-1", "https://x.example/", "https://x.example/", "gb"
    )
    assert captured["default_country"] == "gb"
    assert captured["business_domain"] == "https://x.example/"
    # the transport carries only what the pipeline consumes
    assert scan["phones"] == []
    assert scan["business_id"] == "biz-1"


def test_website_contacts_override_osint_scraped(monkeypatch, tmp_path):
    """A contact confirmed on the business's own website beats an OSINT guess.

    Regression: OSINT numbers scraped from search snippets are best-effort and
    were previously kept over the number the business publishes on its site
    (e.g. a wrong-city landline). The website now wins for both phone and email.
    """
    _setup(monkeypatch, tmp_path)

    with session_scope() as db:
        user = User(email="override@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Clinics in Pune",
                        parsed_category="Clinic", parsed_location="Pune",
                        max_results=1, provider="mock")
        db.add(job)
        db.flush()
        biz = Business(job_id=job.id, provider="mock", provider_place_id="ov_1",
                       name="Override Clinic", category="Clinic",
                       phone="+911234567890", phone_normalized="+911234567890",
                       phone_source="yahoo_web",
                       email="guess@other-domain.com", email_confidence="MEDIUM",
                       website="https://overrideclinic.example.com")
        db.add(biz)
        db.flush()
        db.add(BusinessContact(business_id=biz.id, phone="+911234567890",
                               phone_normalized="+911234567890", source="osint",
                               confidence="HIGH"))
        db.add(BusinessContact(business_id=biz.id, email="guess@other-domain.com",
                               source="osint", confidence="MEDIUM"))
        db.commit()
        biz_id = biz.id

    scan = {
        "pages": [{"url": "https://overrideclinic.example.com/contact",
                   "page_type": "contact", "status": "ok", "http_status": 200,
                   "emails": 1, "phones": 1, "duration_ms": 10}],
        "emails": [{"email": "info@overrideclinic.example.com",
                    "source_url": "https://overrideclinic.example.com/contact",
                    "confidence": "HIGH", "context": "", "disposable": None}],
        "phones": [{"raw": "+91 98765 00000", "confidence": "HIGH",
                    "source_url": "https://overrideclinic.example.com"}],
        "ok": True,
        "summary": "1/1 pages ok, 1 emails, 1 phones",
    }
    with session_scope() as db:
        biz = db.get(Business, biz_id)
        _apply_scan_results(db, biz, scan)
        db.commit()

    with session_scope() as db:
        biz = db.get(Business, biz_id)
        assert biz.phone_normalized == "+919876500000"
        assert biz.phone_source == "website"
        assert biz.email == "info@overrideclinic.example.com"
        assert biz.email_confidence == "HIGH"
        # the OSINT rows are preserved for audit
        sources = {(c.source, c.email) for c in
                   db.query(BusinessContact).filter_by(business_id=biz_id).all()}
        assert ("osint", "guess@other-domain.com") in sources


def test_website_does_not_downgrade_provider_email(monkeypatch, tmp_path):
    """A weak website email must not replace a stronger provider email."""
    _setup(monkeypatch, tmp_path)

    with session_scope() as db:
        user = User(email="nodown@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Clinics in Pune",
                        parsed_category="Clinic", parsed_location="Pune",
                        max_results=1, provider="mock")
        db.add(job)
        db.flush()
        biz = Business(job_id=job.id, provider="mock", provider_place_id="nd_1",
                       name="Provider Email Clinic", category="Clinic",
                       email="owner@providerclinic.com", email_confidence="HIGH",
                       website="https://providerclinic.com")
        db.add(biz)
        db.flush()
        db.add(BusinessContact(business_id=biz.id, email="owner@providerclinic.com",
                               source="provider", confidence="HIGH"))
        db.commit()
        biz_id = biz.id

    scan = {
        "pages": [{"url": "https://providerclinic.com", "page_type": "home",
                   "status": "ok", "http_status": 200,
                   "emails": 1, "phones": 0, "duration_ms": 5}],
        "emails": [{"email": "someone@far-away-directory.net",
                    "source_url": "https://providerclinic.com",
                    "confidence": "LOW", "context": "", "disposable": None}],
        "phones": [],
        "ok": True,
        "summary": "1/1 pages ok",
    }
    with session_scope() as db:
        biz = db.get(Business, biz_id)
        _apply_scan_results(db, biz, scan)
        db.commit()

    with session_scope() as db:
        biz = db.get(Business, biz_id)
        assert biz.email == "owner@providerclinic.com"
        assert biz.email_confidence == "HIGH"


def test_pipeline_provider_failure_marks_job_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/fail.db")
    monkeypatch.setenv("PDF_STORAGE_PATH", str(tmp_path / "reports"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.models import database as db_mod

    db_mod.reset_engine()
    Base.metadata.create_all(db_mod.get_engine())

    from app.providers.base import PlaceSearchQuery, ProviderTimeout
    from app.providers.factory import get_provider

    class TimeoutProvider:
        name = "mock"

        def search_businesses(self, query: PlaceSearchQuery):
            raise ProviderTimeout("boom")

        def get_business_details(self, place_id, language="en"):
            return None

        def health_check(self):
            return True

    monkeypatch.setattr(
        "app.workers.pipeline.search_with_fallback",
        lambda *a, **kw: ([], "mock", ProviderTimeout("boom")),
    )

    with session_scope() as db:
        user = User(email="fail@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Hospitals somewhere",
                        parsed_category="Hospitals", parsed_location="Somewhere",
                        max_results=5, provider="mock")
        db.add(job)
        db.commit()
        job_id = job.id

    with session_scope() as db:
        run_pipeline(db, job_id)

    with session_scope() as db:
        job = db.get(SearchJob, job_id)
        assert job.status == "failed"
        assert "boom" in job.error_message
