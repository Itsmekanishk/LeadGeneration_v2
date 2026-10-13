"""API integration tests: auth, RBAC, search lifecycle, admin."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def inline_dispatch(monkeypatch):
    """Run search jobs inline (no Redis/Celery) inside API tests."""
    from app.workers import pipeline as pipeline_mod

    monkeypatch.setattr(
        "app.workers.dispatch.dispatch_search_job",
        lambda job_id: _run_inline(pipeline_mod, job_id),
    )


def _run_inline(pipeline_mod, job_id):
    from app.models.database import session_scope

    with session_scope() as db:
        pipeline_mod.run_pipeline(db, job_id)


# --------------------------------------------------------------------------- 
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "database" in body["checks"]
    assert "provider" in body["checks"]


# --------------------------------------------------------------------------- 
# Auth
# ---------------------------------------------------------------------------


def test_register_login_me(client):
    r = client.post("/api/auth/register", json={
        "email": "alice@user-test-example.com", "password": "password123",
        "full_name": "Alice",
    })
    assert r.status_code == 201
    tokens = r.json()
    assert tokens["access_token"]

    r2 = client.post("/api/auth/login", json={
        "email": "alice@user-test-example.com", "password": "password123",
    })
    assert r2.status_code == 200

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    r3 = client.get("/api/auth/me", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["email"] == "alice@user-test-example.com"


def test_login_wrong_password(client, user_tokens):
    r = client.post("/api/auth/login", json={
        "email": "user@user-test-example.com", "password": "wrongpassword",
    })
    assert r.status_code == 401


def test_me_requires_auth(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_password_reset_flow(client, user_tokens):
    r = client.post("/api/auth/forgot-password", json={
        "email": "user@user-test-example.com"})
    assert r.status_code == 200
    token = r.json().get("dev_token")
    assert token  # non-production environment returns the token for testing
    r2 = client.post("/api/auth/reset-password", json={
        "token": token, "new_password": "newpassword123",
    })
    assert r2.status_code == 200
    r3 = client.post("/api/auth/login", json={
        "email": "user@user-test-example.com", "password": "newpassword123",
    })
    assert r3.status_code == 200


def test_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={
        "email": "short@user-test-example.com", "password": "short",
    })
    assert r.status_code in (400, 422)


# --------------------------------------------------------------------------- 
# Search lifecycle (mock provider; crawling disabled in conftest)
# ---------------------------------------------------------------------------


def test_interpret_endpoint(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search/interpret", json={
        "free_text": "Hospitals in Porwal Road Pune",
    }, headers=headers)
    assert r.status_code == 200
    interpreted = r.json()["interpreted"]
    assert interpreted["category"] == "Hospitals"
    assert "Porwal Road" in (interpreted["location"] or "")


def test_create_search_runs_inline_and_completes(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 10,
    }, headers=headers)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = client.get(f"/api/search/{job_id}", headers=headers).json()
    assert status["status"] == "completed"
    assert status["progress_stats"]["total"] == 10

    results = client.get(f"/api/search/{job_id}/results", headers=headers).json()
    assert results["total"] == 10
    assert all(item["name"] for item in results["items"])


def test_create_search_requires_query(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={"max_results": 10}, headers=headers)
    assert r.status_code == 422


def test_empty_results_scenario(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "empty hospitals somewhere", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    results = client.get(f"/api/search/{job_id}/results", headers=headers).json()
    assert results["total"] == 0
    summary = client.get(f"/api/search/{job_id}/summary", headers=headers).json()
    assert summary["summary"]["total"] == 0


def test_job_ownership_enforced(client, user_tokens, admin_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Aundh Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    # admin may view (RBAC: admin sees all jobs)
    r2 = client.get(f"/api/search/{job_id}", headers=admin_tokens[0])
    assert r2.status_code == 200
    # anonymous may not
    r3 = client.get(f"/api/search/{job_id}")
    assert r3.status_code == 401


def test_search_validation_rejects_bad_provider(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Pune", "provider": "hax",
    }, headers=headers)
    assert r.status_code == 422


def test_rate_limit_blocks_flood(client, user_tokens, monkeypatch):
    headers = user_tokens[0]
    monkeypatch.setenv("LOCALLEAD_TEST_SEARCH_LIMIT", "2")
    for _ in range(2):
        r = client.post("/api/search", json={
            "free_text": "empty fields", "max_results": 3}, headers=headers)
        assert r.status_code == 202
    r3 = client.post("/api/search", json={
        "free_text": "empty fields", "max_results": 3}, headers=headers)
    assert r3.status_code == 429


# --------------------------------------------------------------------------- 
# RBAC
# ---------------------------------------------------------------------------


def test_admin_endpoints_require_admin(client, user_tokens):
    headers = user_tokens[0]
    for path in ("/api/admin/jobs", "/api/admin/users", "/api/admin/usage"):
        r = client.get(path, headers=headers)
        assert r.status_code == 403


def test_admin_endpoints_work_for_admin(client, admin_tokens):
    headers = admin_tokens[0]
    assert client.get("/api/admin/jobs", headers=headers).status_code == 200
    assert client.get("/api/admin/users", headers=headers).status_code == 200
    usage = client.get("/api/admin/usage", headers=headers)
    assert usage.status_code == 200
    assert "today_requests" in usage.json()


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------------------
# Search deletion (history page "delete" action)
# ---------------------------------------------------------------------------


def test_delete_completed_search(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    dr = client.delete(f"/api/search/{job_id}", headers=headers)
    assert dr.status_code == 204
    # job and its results are gone afterwards
    assert client.get(f"/api/search/{job_id}", headers=headers).status_code == 404
    assert client.get(f"/api/search/{job_id}/results", headers=headers).status_code == 404


def test_delete_running_search_conflict(client, user_tokens, monkeypatch):
    headers = user_tokens[0]
    # keep the job queued: override the inline dispatcher for this test
    monkeypatch.setattr("app.workers.dispatch.dispatch_search_job", lambda job_id: None)
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Kharadi Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]

    dr = client.delete(f"/api/search/{job_id}", headers=headers)
    assert dr.status_code == 409
    # the queued job is untouched
    assert client.get(f"/api/search/{job_id}", headers=headers).status_code == 200


def test_delete_requires_auth(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    assert client.delete(f"/api/search/{job_id}").status_code == 401
    assert r.headers.get("x-frame-options") == "DENY"


# ---------------------------------------------------------------------------
# Result completeness: every discovered contact, sortable columns
# ---------------------------------------------------------------------------


PRIMARY_EMAIL = "seed-primary@clinic-seed.example"
EXTRA_EMAIL = "seed-appointments@clinic-seed.example"
EXTRA_PHONE = "+919000100002"


def _seed_contacts(job_id: str) -> str:
    """Attach one primary plus two extra contacts to a job's first lead."""
    from app.models.database import session_scope
    from app.models.models import Business, BusinessContact

    with session_scope() as db:
        biz = db.query(Business).filter(Business.job_id == job_id).first()
        assert biz is not None
        biz_id = biz.id
        # Drop whatever the pipeline recorded so the contact set is exact.
        db.query(BusinessContact).filter(
            BusinessContact.business_id == biz_id).delete()
        biz.phone = "+91 90001 00001"
        biz.phone_normalized = "+919000100001"
        biz.email = PRIMARY_EMAIL
        biz.email_confidence = "HIGH"
        db.add_all([
            BusinessContact(business_id=biz_id, email=PRIMARY_EMAIL,
                            confidence="HIGH", source="crawl"),
            BusinessContact(business_id=biz_id, email=EXTRA_EMAIL,
                            confidence="MEDIUM", source="crawl"),
            BusinessContact(business_id=biz_id, email="seed-ceo@clinic-seed.example",
                            confidence="LOW", source="osint"),
            BusinessContact(business_id=biz_id, phone="+91 90001 00002",
                            phone_normalized=EXTRA_PHONE,
                            confidence="MEDIUM", source="crawl"),
        ])
    return biz_id


def test_results_expose_every_discovered_contact(client, user_tokens):
    """The results list must carry all contacts, not just the primary one."""
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    biz_id = _seed_contacts(job_id)

    page = client.get(f"/api/search/{job_id}/results", headers=headers).json()
    lead = next(i for i in page["items"] if i["id"] == biz_id)

    # primary first, then the rest ordered by confidence
    assert lead["emails"] == [
        PRIMARY_EMAIL, EXTRA_EMAIL, "seed-ceo@clinic-seed.example",
    ]
    assert lead["phones"] == ["+919000100001", EXTRA_PHONE]
    # the headline single-value fields are unchanged
    assert lead["email"] == PRIMARY_EMAIL


def test_results_contacts_never_duplicated_across_rows(client, user_tokens):
    """Each lead only reports its own contacts."""
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    biz_id = _seed_contacts(job_id)
    page = client.get(f"/api/search/{job_id}/results", headers=headers).json()

    for item in page["items"]:
        if item["id"] == biz_id:
            continue
        assert EXTRA_EMAIL not in item["emails"]
        assert EXTRA_PHONE not in item["phones"]
        # the lead's own primary contact still leads its list
        assert item["emails"][:1] == ([item["email"]] if item["email"] else [])


def test_summary_counts_all_discovered_contacts(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    _seed_contacts(job_id)
    summary = client.get(f"/api/search/{job_id}/summary", headers=headers).json()["summary"]
    assert summary["emails_total"] >= 2
    assert summary["phones_total"] >= 2


def test_results_sort_is_honoured(client, user_tokens):
    """The sort parameter the UI sends must actually reorder the page."""
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 10,
    }, headers=headers)
    job_id = r.json()["job_id"]

    for key in ("rating", "review_count"):
        items = client.get(
            f"/api/search/{job_id}/results?sort={key}", headers=headers
        ).json()["items"]
        values = [i[key] for i in items if i[key] is not None]
        assert values == sorted(values, reverse=True), key
        # leads with no value for the sort key stay at the end
        rank = [1 if i[key] is None else 0 for i in items]
        assert rank == sorted(rank), key

    by_name = client.get(
        f"/api/search/{job_id}/results?sort=name", headers=headers
    ).json()["items"]
    names = [i["name"] for i in by_name]
    assert names == sorted(names)


def test_results_rejects_unknown_sort(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    resp = client.get(f"/api/search/{job_id}/results?sort=bogus", headers=headers)
    assert resp.status_code == 422


SECONDARY_ONLY_EMAIL = "seed-secondary-only@clinic-seed.example"


def _seed_secondary_only_lead(job_id: str) -> str:
    """A lead with no primary contact — its email lives only on a contact row."""
    from app.models.database import session_scope
    from app.models.models import Business, BusinessContact

    with session_scope() as db:
        biz = db.query(Business).filter(Business.job_id == job_id).first()
        assert biz is not None
        biz_id = biz.id
        db.query(BusinessContact).filter(
            BusinessContact.business_id == biz_id).delete()
        biz.email = ""
        biz.email_confidence = ""
        biz.phone = ""
        biz.phone_normalized = ""
        db.add(BusinessContact(
            business_id=biz_id, email=SECONDARY_ONLY_EMAIL,
            confidence="HIGH", source="crawl",
        ))
    return biz_id


def test_results_contact_counts_are_reported(client, user_tokens):
    """contact_count mirrors the real number of values behind each lead."""
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    biz_id = _seed_contacts(job_id)

    page = client.get(f"/api/search/{job_id}/results", headers=headers).json()
    lead = next(i for i in page["items"] if i["id"] == biz_id)
    assert lead["contact_count"] == len(lead["emails"]) + len(lead["phones"])
    assert lead["contact_count"] == 5


def test_results_has_email_sees_secondary_only_contacts(client, user_tokens):
    """A lead whose email was only found on a sub-page still counts as 'has email'."""
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    biz_id = _seed_secondary_only_lead(job_id)

    with_email = client.get(
        f"/api/search/{job_id}/results?has_email=true", headers=headers
    ).json()["items"]
    ids = {i["id"] for i in with_email}
    assert biz_id in ids
    seeded = next(i for i in with_email if i["id"] == biz_id)
    assert seeded["email"] == ""  # headline stays empty; the contact row carries it
    assert SECONDARY_ONLY_EMAIL in seeded["emails"]

    without_email = client.get(
        f"/api/search/{job_id}/results?has_email=false", headers=headers
    ).json()["items"]
    assert biz_id not in {i["id"] for i in without_email}

    no_phone = client.get(
        f"/api/search/{job_id}/results?has_phone=false", headers=headers
    ).json()["items"]
    assert biz_id in {i["id"] for i in no_phone}


def test_results_search_matches_secondary_contacts(client, user_tokens):
    """Free-text search reaches contacts stored on sub-pages too."""
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    biz_id = _seed_secondary_only_lead(job_id)

    hits = client.get(
        f"/api/search/{job_id}/results?q=secondary-only", headers=headers
    ).json()["items"]
    assert biz_id in {i["id"] for i in hits}


def test_results_sort_contacts_puts_richest_leads_first(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 10,
    }, headers=headers)
    job_id = r.json()["job_id"]
    _seed_contacts(job_id)

    items = client.get(
        f"/api/search/{job_id}/results?sort=contacts", headers=headers
    ).json()["items"]
    counts = [i["contact_count"] for i in items]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] >= 2


def test_business_detail_exposes_every_discovered_contact(client, user_tokens):
    """The audit drawer's contact lists must be populated, not empty.

    The detail payload is built from the ORM row, which has no emails/phones
    attributes — without attaching contacts they silently came back as [].
    """
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    biz_id = _seed_contacts(job_id)

    resp = client.get(f"/api/business/{biz_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["emails"] == [
        PRIMARY_EMAIL, EXTRA_EMAIL, "seed-ceo@clinic-seed.example",
    ]
    assert body["phones"] == ["+919000100001", EXTRA_PHONE]
    assert body["contact_count"] == 5
    assert len(body["contacts"]) == 4
