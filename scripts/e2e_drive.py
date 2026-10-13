"""Headless end-to-end drive of the LocalLead backend (mock provider).

Lifecycle exercised:
  register -> interpret -> create search -> poll job -> results -> summary
  -> CSV export (create + download) -> DELETE -> 404 afterwards
  + negative auth / validation checks.

Run: backend/.venv/bin/python scripts/e2e_drive.py
"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api"
EMAIL = f"e2e+{int(time.time())}@lltest.dev"
PASSWORD = "Str0ngPass!x"


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"FAILED: {name}")


def main():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        # --- 1. register ---------------------------------------------------
        r = c.post("/auth/register", json={
            "email": EMAIL, "password": PASSWORD, "full_name": "E2E Tester"})
        check("register returns 201", r.status_code == 201, f"got {r.status_code}")
        tok = r.json()
        check("register returns access token", bool(tok.get("access_token")))
        h = {"Authorization": f"Bearer {tok['access_token']}"}

        # --- 2. negative: unauthenticated search listing is rejected -------
        r = c.get("/search")
        check("unauthenticated /search is 401", r.status_code == 401,
              f"got {r.status_code}")

        # --- 3. interpret (natural language -> structured query) -----------
        r = c.post("/search/interpret", headers=h,
                   json={"free_text": "coffee shops in Austin with emails"})
        check("interpret 200", r.status_code == 200, f"got {r.status_code}")
        interp = r.json()
        loc = (interp.get("interpreted") or {}).get("location")
        check("interpret extracted a location", bool(loc), f"location={loc!r}")

        # --- 4. create search job ------------------------------------------
        body = {
            "free_text": "coffee shops in Austin",
            "category": "coffee shops",
            "location": "Austin",
            "max_results": 10,
            "country": "us",
            "provider": "mock",
        }
        r = c.post("/search", headers=h, json=body)
        check("create search 202", r.status_code == 202, f"got {r.status_code} {r.text[:200]}")
        job = r.json()
        job_id = job.get("job_id") or job.get("id")
        check("job id returned", bool(job_id), str(job)[:150])

        # --- 5. poll until terminal state -----------------------------------
        terminal = {"completed", "partial", "failed", "cancelled"}
        state, last = None, None
        for _ in range(60):
            r = c.get(f"/search/{job_id}", headers=h)
            last = r.json()
            state = last.get("status")
            if state in terminal:
                break
            time.sleep(0.5)
        check("job reaches a terminal state", state in terminal, f"status={state}")

        # --- 6. results ------------------------------------------------------
        r = c.get(f"/search/{job_id}/results", headers=h)
        check("results 200", r.status_code == 200, f"got {r.status_code}")
        res = r.json()
        items = res.get("items", res if isinstance(res, list) else [])
        check("results contain businesses", len(items) > 0, f"n={len(items)}")
        first = items[0]
        check("business has name", bool(first.get("name")), str(first)[:120])

        # --- 7. summary ------------------------------------------------------
        r = c.get(f"/search/{job_id}/summary", headers=h)
        check("summary 200", r.status_code == 200, f"got {r.status_code}")

        # --- 8. CSV export: create + download --------------------------------
        r = c.post(f"/search/{job_id}/export/csv", headers=h)
        check("export create 200/201", r.status_code in (200, 201),
              f"got {r.status_code} {r.text[:150]}")
        fname = r.json().get("filename")
        check("export returns filename", bool(fname), str(r.json())[:120])
        r = c.get(f"/search/{job_id}/export/csv", headers=h, params={"filename": fname})
        check("export download 200", r.status_code == 200, f"got {r.status_code}")
        check("CSV body has header+rows", r.text.count(",") > 3 and "\n" in r.text,
              f"{len(r.text)} bytes")

        # --- 9. DELETE the completed search ----------------------------------
        r = c.delete(f"/search/{job_id}", headers=h)
        check("DELETE 204", r.status_code == 204, f"got {r.status_code}")
        r = c.get(f"/search/{job_id}", headers=h)
        check("deleted job is 404", r.status_code == 404, f"got {r.status_code}")

        # --- 10. history no longer lists it -----------------------------------
        r = c.get("/search", headers=h)
        ids = [j.get("job_id") or j.get("id") for j in r.json()]
        check("deleted job absent from history", job_id not in ids, f"n_jobs={len(ids)}")

    print("\nALL E2E CHECKS PASSED")


if __name__ == "__main__":
    main()
