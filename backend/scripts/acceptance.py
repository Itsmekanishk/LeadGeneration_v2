"""Acceptance test: drives the real FastAPI app over HTTP (no Redis/Celery
required — jobs execute via the inline dispatch hook when Redis is absent).

Run: ./.venv/bin/python scripts/acceptance.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ["DATABASE_URL"] = "sqlite:///./data/acceptance.db"
os.environ["BUSINESS_PROVIDER"] = "mock"
os.environ["SECRET_KEY"] = "acceptance-test-secret-key-16x"
os.environ["PDF_STORAGE_PATH"] = "./data/reports"
os.environ["ENABLE_WEBSITE_CRAWLING"] = "false"
os.environ["CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC"] = "1000"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


def main() -> int:
    import shutil

    shutil.rmtree("data", ignore_errors=True)
    Path("data").mkdir(parents=True)

    from app.main import app
    from app.models.base import Base
    from app.models.database import get_engine

    Base.metadata.create_all(get_engine())

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))

    with TestClient(app) as client:
        # 1. health
        r = client.get("/api/health")
        check("health endpoint", r.status_code == 200 and r.json()["status"] == "ok",
              str(r.json().get("checks", {})))

        # 2. register (first user becomes admin)
        r = client.post("/api/auth/register", json={
            "email": "owner@acceptance-example.com",
            "password": "password123", "full_name": "Acceptance User"})
        check("register + first-user-is-admin",
              r.status_code == 201 and r.json()["user"]["role"] == "ADMIN")
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # 3. interpret query
        r = client.post("/api/search/interpret",
                        json={"free_text": "Hospitals in Porwal Road Pune"},
                        headers=headers)
        interp = r.json()["interpreted"]
        check("query parsed (Hospitals / Porwal Road, Pune)",
              r.status_code == 200 and interp["category"] == "Hospitals"
              and "Porwal Road" in (interp["location"] or "")
              and "Pune" in (interp["location"] or ""),
              f"{interp['category']} | {interp['location']}")

        # 4. create job (inline dispatch)
        from app.workers import pipeline as pipeline_mod

        def inline(job_id: str) -> None:
            from app.models.database import session_scope

            with session_scope() as db:
                pipeline_mod.run_pipeline(db, job_id)

        import app.workers.dispatch as dispatch_mod

        original = dispatch_mod.dispatch_search_job
        dispatch_mod.dispatch_search_job = inline
        try:
            r = client.post("/api/search", json={
                "free_text": "Hospitals in Porwal Road Pune", "max_results": 12},
                headers=headers)
            check("job created (202, queued)",
                  r.status_code == 202 and r.json()["status"] == "queued")
            job_id = r.json()["job_id"]

            # 5. poll to completion
            final = None
            for _ in range(60):
                final = client.get(f"/api/search/{job_id}", headers=headers).json()
                if final["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(0.2)
            check("job completed", final is not None and final["status"] == "completed",
                  f"progress={final['progress'] if final else '?'}")
            stats = final["progress_stats"]
            check("12 businesses discovered", stats["total"] == 12, str(stats))
            check("phones normalized", stats["phones_found"] > 0,
                  f'{stats["phones_found"]} phones')

            # 6. results + filter
            results = client.get(f"/api/search/{job_id}/results?page=1&page_size=25",
                                 headers=headers).json()
            check("results paginated", results["total"] == 12 and len(results["items"]) == 12)
            filtered = client.get(
                f"/api/search/{job_id}/results?has_phone=true&page_size=25",
                headers=headers).json()
            check("filter has_phone works",
                  filtered["total"] == stats["phones_found"],
                  f'{filtered["total"]} rows')

            # provenance retained
            first = results["items"][0]
            check("source attribution retained", bool(first["source"]), first["source"][:50])

            # 7. summary
            summary = client.get(f"/api/search/{job_id}/summary", headers=headers).json()
            check("summary endpoint", summary["summary"]["total"] == 12)

            # 8. exports
            for fmt, mime in (("pdf", "application/pdf"), ("csv", "text/csv"),
                              ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")):
                gen = client.post(f"/api/search/{job_id}/export/{fmt}", headers=headers)
                ok = gen.status_code == 200 and gen.json()["filename"].startswith("report_")
                dl = client.get(
                    f"/api/search/{job_id}/export/{fmt}?filename={gen.json()['filename']}",
                    headers=headers) if ok else None
                check(f"{fmt.upper()} generated + downloadable",
                      ok and dl is not None and dl.status_code == 200
                      and dl.headers["content-type"].startswith(mime),
                      f"{gen.json()['filename']}")

            # 9. ownership: anonymous cannot access
            anon = client.get(f"/api/search/{job_id}/results")
            check("anonymous blocked (401)", anon.status_code == 401)

            # 10. secrets not exposed
            leak = client.get("/api/openapi.json")
            body = leak.text.lower()
            check("no secrets in OpenAPI", "test-secret" not in body
                  and "acceptance-test-secret" not in body)
        finally:
            dispatch_mod.dispatch_search_job = original

    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} acceptance checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
