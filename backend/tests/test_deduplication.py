"""Deduplication engine tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.base import Base  # noqa: E402
from app.models.database import get_engine, session_scope  # noqa: E402
from app.models.models import Business, SearchJob, User  # noqa: E402
from app.services.deduplication import DuplicateResolver, score_pair  # noqa: E402
from app.utils.datetime_utils import utcnow  # noqa: E402


def _mk_business(**kwargs):
    defaults = dict(
        job_id="job", provider="mock", provider_place_id="p", name="A",
        category="Hospital", address="", phone="", phone_normalized="",
        website="", website_normalized="", source="test",
        created_at=utcnow(), updated_at=utcnow(),
    )
    defaults.update(kwargs)
    return Business(**defaults)


def test_score_identical_website():
    a = _mk_business(name="Sunrise Hospital", website_normalized="sunrisehospital.in")
    b = _mk_business(name="Sunrise Hosp", website_normalized="sunrisehospital.in")
    score, reasons = score_pair(a, b)
    assert score == 1.0
    assert "identical normalized website" in reasons[0]


def test_score_identical_phone():
    a = _mk_business(name="Clinic One", phone_normalized="+919876543210")
    b = _mk_business(name="Clinic 1", phone_normalized="+919876543210")
    score, _ = score_pair(a, b)
    assert score == 1.0


def test_score_distinct_businesses_low():
    a = _mk_business(name="Sunrise Hospital", address="1 Porwal Road, Pune")
    b = _mk_business(name="Apex Dental", address="99 FC Road, Pune")
    score, _ = score_pair(a, b)
    assert score < 0.6


def test_resolver_merges_duplicates(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/dedup.db")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.models import database as db_mod

    db_mod.reset_engine()
    Base.metadata.create_all(db_mod.get_engine())

    with session_scope() as db:
        user = User(email="d@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="q", provider="mock")
        db.add(job)
        db.flush()
        db.add_all([
            Business(job_id=job.id, provider="mock", provider_place_id="1",
                     name="Sunrise Hospital", website_normalized="sunrise.in",
                     created_at=utcnow(), updated_at=utcnow()),
            Business(job_id=job.id, provider="mock", provider_place_id="2",
                     name="Sunrise Hospital", website_normalized="sunrise.in",
                     created_at=utcnow(), updated_at=utcnow()),
            Business(job_id=job.id, provider="mock", provider_place_id="3",
                     name="Apex Dental", website_normalized="apex.in",
                     created_at=utcnow(), updated_at=utcnow()),
        ])
        job_id = job.id

    with session_scope() as db:
        groups = DuplicateResolver(db).resolve_job(job_id)
        assert len(groups) == 1
        assert groups[0].confidence == 1.0
        statuses = {b.provider_place_id: b.dedup_status
                    for b in db.query(Business).filter_by(job_id=job_id)}
        assert statuses["1"] == "unique"
        assert statuses["2"] == "duplicate"
        assert statuses["3"] == "unique"
