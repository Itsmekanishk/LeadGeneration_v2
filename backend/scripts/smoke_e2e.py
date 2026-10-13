"""End-to-end pipeline smoke test (SQLite, no network, mock provider+crawler).

Run:  ./.venv/bin/python scripts/smoke_e2e.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./data/smoke.db"
os.environ["BUSINESS_PROVIDER"] = "mock"
os.environ["PDF_STORAGE_PATH"] = "./data/reports"
os.environ["CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC"] = "50"  # no throttle for smoke

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


def main() -> int:
    from app.core.logging_config import setup_logging

    setup_logging()
    from app.models.base import Base
    from app.models.database import get_engine, session_scope
    from app.models.models import Business, SearchJob, User
    from app.services import auth_service
    from app.services.query_parser import parse_query
    from app.services.deduplication import DuplicateResolver
    from app.workers.pipeline import run_pipeline
    from unittest.mock import patch

    import shutil

    shutil.rmtree("data", ignore_errors=True)
    Path("data").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine())

    with session_scope() as db:
        user = auth_service.create_user(db, email="admin@locallead.test",
                                        password="password123", full_name="Admin")
        job = SearchJob(
            user_id=user.id, query_raw="Hospitals in Porwal Road Pune",
            parsed_category="Hospitals", parsed_location="Porwal Road, Pune",
            max_results=12, provider="mock", status="queued",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    with session_scope() as db:
        run_pipeline(db, job_id)

    with session_scope() as db:
        job = db.get(SearchJob, job_id)
        print("job status:", job.status)
        print("stats:", dict(
            total=job.total, completed=job.completed, failed=job.failed,
            emails=job.emails_found, phones=job.phones_found,
            websites=job.websites_found))
        businesses = db.query(Business).filter(Business.job_id == job_id).all()
        for b in businesses[:5]:
            print(f"  - {b.name:34} phone={b.phone_normalized or '—':16} "
                  f"email={b.email or '—':30} site={b.website_normalized or '—'}")

        # exports
        from app.services.export_service import generate_export

        pdf_name, n = generate_export(db, job, "pdf", user.id)
        csv_name, _ = generate_export(db, job, "csv", user.id)
        xlsx_name, _ = generate_export(db, job, "xlsx", user.id)
        print("exports:", pdf_name, csv_name, xlsx_name, f"({n} rows)")

    out = Path("data/reports")
    for f in sorted(out.glob("*")):
        print(f"  {f.name}  {f.stat().st_size} bytes")
        shutil.copy(f, Path("examples") / f.name) if False else None
    return 0


if __name__ == "__main__":
    sys.exit(main())
