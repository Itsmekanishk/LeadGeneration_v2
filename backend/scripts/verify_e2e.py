"""Headless end-to-end behavioral verification for LocalLead.

Stands up a real local HTTP server serving synthetic business sites, then
drives the REAL pipeline (mock provider discovery → persist → crawl →
extract → dedup → metrics) against it and asserts actual state transitions
and persisted outputs. Finished with the REAL export service (PDF/CSV/XLSX).

The only seam: discovered mock-provider websites (*.
.example.com) are redirected to 127.0.0.1:8901 before crawling (equivalent to
a /etc/hosts override in an integration rig). The crawl, extraction,
normalization, persistence and dedup code that runs is the real code.

Run: cd backend && ./.venv/bin/python scripts/verify_e2e.py
"""
from __future__ import annotations

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Config BEFORE any app import (get_settings is cached) -------------------
os.environ["DATABASE_URL"] = "sqlite:///./data/verify_e2e.db"
os.environ["BUSINESS_PROVIDER"] = "mock"
os.environ["SECRET_KEY"] = "verify-e2e-secret-key-not-for-prod-0001"
os.environ["PDF_STORAGE_PATH"] = "./data/verify_reports"
os.environ["ENABLE_WEBSITE_CRAWLING"] = "true"
os.environ["ENABLE_DISIFY_CHECKS"] = "false"      # no third-party calls
os.environ["ENABLE_NOMINATIM_GEOCODE"] = "false"  # no third-party calls
os.environ["CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC"] = "1000"
os.environ["CRAWL_JOB_CONCURRENCY"] = "4"
os.environ["MAX_PAGES_PER_DOMAIN"] = "5"
os.environ["SSRF_ALLOWED_HOSTS"] = "127.0.0.1"    # test-rig allowlist (per test_ssrf.py)

# ----------------------------------------------------------------------------
# 1. Fake business websites on 127.0.0.1
# ----------------------------------------------------------------------------
def cf_encode(email: str) -> str:
    """Encode an email the way Cloudflare's email-protection does."""
    key = 0x37
    return f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in email)

CF_EMAIL = "hidden@novaclinic-rig.com"   # NB: *.example.com is junk-filtered by the extractor
CF_HEX = cf_encode(CF_EMAIL)

class SiteHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request log
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        host = (self.headers.get("Host") or "").split(":")[0]

        def send(body: str, code: int = 200):
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        if path == "/robots.txt":  # explicit allow-all
            send("User-agent: *\nAllow: /\n")
            return

        # Sites are routed by path prefix; the homepage uses RELATIVE links so
        # the crawler's urljoin keeps the prefix (mimics a real origin).

        # --- Site 1: plain site; email (mailto + text) and phone on /contact-us
        if path == "/apexclinic":
            send("<html><body><h1>Apex Clinic</h1>"
                 "<a href='contact-us'>Contact us</a> "
                 "<a href='team'>Our team</a> "
                 "<a href='https://facebook.com/apex'>FB</a></body></html>")
        elif path == "/apexclinic/contact-us":
            send("<html><body>Contact us at <a href='mailto:info@apexclinic-rig.com'>"
                 "info@apexclinic-rig.com</a> or call +91 98220 12345.</body></html>")
        elif path == "/apexclinic/team":
            send("<html><body>not found</body></html>", 404)  # exercise 404 path

        # --- Site 2: Cloudflare-obfuscated email only (new decoder path)
        elif path == "/novaclinic":
            send("<html><body><h1>Nova Clinic</h1>"
                 f"<a href='/cdn-cgi/l/email-protection#{CF_HEX}'>Email us</a> "
                 "<a href='contact'>Contact</a></body></html>")
        elif path == "/novaclinic/contact":
            send("<html><body><h2>Reach Nova Clinic</h2>"
                 f"<span data-cfemail='{CF_HEX}'></span>"
                 "Phone: +91 20 6650 1234</body></html>")

        # --- Site 3: plain-text email only; provider gave NO phone for this
        #     business, so the website phone must be promoted to business.phone
        elif path == "/primeclinic":
            send("<html><body><h1>Prime Clinic</h1>"
                 "<a href='contact'>Contact</a></body></html>")
        elif path == "/primeclinic/contact":
            send("<html><body>Mail hello@primeclinic-rig.com "
                 "| Tel +91 98220 99999</body></html>")

        else:
            send("<html><body>unknown host</body></html>", 404)

server = ThreadingHTTPServer(("127.0.0.1", 8901), SiteHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(f"[1] fake business sites on http://127.0.0.1:8901 (cfemail hex={CF_HEX[:16]}…)")

# ----------------------------------------------------------------------------
# 2. App imports (after env)
# ----------------------------------------------------------------------------
from app.core.config import get_settings

get_settings.cache_clear()
settings = get_settings()
assert settings.enable_website_crawling is True, "crawling must be on"

import app.workers.pipeline as pipeline_mod
from app.crawlers.contact_finder import _decode_cloudflare_email
from app.core.security import hash_password
from app.models import database as db_mod
from app.models.base import Base
from app.models.database import session_scope
from app.models.models import Business, BusinessContact, CrawlPage, Export, SearchJob, User
from app.utils.normalization import domain_of
from app.workers.pipeline import run_pipeline

# Unit check of the new decoder itself
assert _decode_cloudflare_email(CF_HEX) == CF_EMAIL, _decode_cloudflare_email(CF_HEX)
print(f"[2] _decode_cloudflare_email({CF_HEX[:12]}…) == {CF_EMAIL}")

# Redirect discovered *.example.com sites to the local rig before crawling.
_orig_scan = pipeline_mod._scan_business_website

_RIG_SITE_MAP = {
    "apexclinic.example.com": "apexclinic",
    "novaclinic.example.com": "novaclinic",
    "primeclinic.example.com": "primeclinic",
    # eliteclinic/everestclinic intentionally NOT mapped → dns_error isolation
}

def _scan_on_local_rig(business_id: str, website: str, domain: str) -> dict:
    """Redirect three mock sites onto the local rig; leave others untouched
    (their DNS lookups fail → exercises per-site failure isolation)."""
    from urllib.parse import urlparse

    p = urlparse(website)
    prefix = _RIG_SITE_MAP.get(domain_of(p.netloc) or "")
    if prefix and p.netloc:
        path = (p.path or "").lstrip("/")
        target = f"http://127.0.0.1:8901/{prefix}/" + (path if path else "")
        return _orig_scan(business_id, target, domain_of(p.netloc))
    return _orig_scan(business_id, website, domain)

pipeline_mod._scan_business_website = _scan_on_local_rig
print("[3] crawl targets redirected: *.example.com → http://127.0.0.1:8901")

# ----------------------------------------------------------------------------
# 3. Seed a user + job, then run the REAL pipeline
# ----------------------------------------------------------------------------
db_mod.reset_engine()
Base.metadata.create_all(db_mod.get_engine())

with session_scope() as db:
    user = User(email="verify-admin@example.com",
                hashed_password=hash_password("pw-verify-123"),
                full_name="Verify Bot", role="ADMIN")
    db.add(user)
    db.flush()
    job = SearchJob(user_id=user.id, query_raw="Clinics in VerifyTown",
                    parsed_category="Clinic", parsed_location="VerifyTown",
                    max_results=6, requested_results=6, provider="mock",
                    status="queued")
    db.add(job)
    db.flush()
    job_id, user_id = job.id, user.id

print(f"[4] job {job_id} → running real pipeline")
with session_scope() as pipe_db:
    run_pipeline(pipe_db, job_id)

# ----------------------------------------------------------------------------
# 4. Assertions on actual persisted state
# ----------------------------------------------------------------------------
failures: list[str] = []

def check(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✔' if cond else '✘'} {label}" + ("" if cond else f"   [{detail}]"))
    if not cond:
        failures.append(label)

with session_scope() as db:
    job = db.get(SearchJob, job_id)
    check(job.status == "completed", "job reached terminal status 'completed'",
          f"status={job.status} detail={job.stage_detail!r} err={job.error_message!r}")
    check(job.progress == 100, "progress advanced to 100", f"progress={job.progress}")

    biz = db.query(Business).filter(Business.job_id == job_id).all()
    total = len(biz)
    check(total > 0, "businesses persisted", f"total={total}")
    check(total <= 6, "respects max_results=6 cap", f"total={total}")

    biz_ids = [b.id for b in biz]
    pages = db.query(CrawlPage).filter(CrawlPage.business_id.in_(biz_ids)).all()

    ok_pages = [p for p in pages if p.status == "ok" and "127.0.0.1:8901" in (p.url or "")]
    check(len(ok_pages) >= 4, "crawl_pages rows for real fetches (home + contact pages)",
          f"pages={[(p.url, p.status, p.http_status) for p in pages][:8]}")
    check(any(p.http_status == 404 for p in pages),
          "a 404 page outcome recorded as http_error/404",
          f"statuses={sorted({p.status for p in pages})}")

    # Per-site failure isolation: some mock sites (*.example.com) have no rig →
    # DNS must fail for them WITHOUT sinking the job.
    dead_sites = [p for p in pages if p.status == "dns_error"]
    check(len(dead_sites) > 0 and job.status == "completed",
          "unreachable site isolated as dns_error; job still completed",
          f"dead={len(dead_sites)}")

    contacts = db.query(BusinessContact).filter(BusinessContact.business_id.in_(biz_ids)).all()
    emails = sorted({c.email for c in contacts if c.email})
    website_contacts = [c for c in contacts if c.source == "website"]
    check(any(e.endswith("@apexclinic-rig.com") for e in emails),
          "email from mailto: on /contact-us persisted", f"emails={emails}")
    check(len(website_contacts) > 0, "website-sourced contacts recorded",
          f"n={len(website_contacts)}")
    check(any((c.source_url or "").startswith("http://127.0.0.1:8901")
              for c in website_contacts),
          "provenance source_url points at crawled URL")

    # ---- The NEW feature: Cloudflare-obfuscated email decoded end-to-end
    cf_rows = [c for c in contacts if (c.email or "").endswith("@novaclinic-rig.com")]
    check(len(cf_rows) == 1, "NEW: Cloudflare-obfuscated email decoded & stored",
          f"emails={emails}")
    if cf_rows:
        check(cf_rows[0].email == CF_EMAIL, "NEW: decoded address matches original",
              f"got {cf_rows[0].email!r}")
        check((cf_rows[0].source_url or "").startswith("http://127.0.0.1:8901"),
              "NEW: cfemail provenance recorded", f"src={cf_rows[0].source_url!r}")

    check(any(c.source == "provider" and c.phone for c in contacts),
          "provider phones persisted", "")

    by_name = {b.name or "": b for b in biz}
    check("Sunrise" in "".join(by_name),
          "website-less business still persisted (skipped from crawl)",
          f"names={list(by_name)[:8]}")
    cf_biz = next((b for n, b in by_name.items() if "Nova" in n), None)
    if cf_biz:
        check(cf_biz.email == CF_EMAIL, "NEW: business.email promoted to decoded cfemail",
              f"email={cf_biz.email!r}")
        check(cf_biz.email_confidence in ("HIGH", "MEDIUM", "LOW"),
              "NEW: decoded email carries a confidence tier",
              f"conf={cf_biz.email_confidence!r}")
    apex_biz = next((b for n, b in by_name.items() if "Apex" in n), None)
    if apex_biz:
        check(apex_biz.email == "info@apexclinic-rig.com",
              "business.email = best website email", f"email={apex_biz.email!r}")
        check(bool(apex_biz.phone_normalized),
              "phone normalized", f"phone={apex_biz.phone!r} norm={apex_biz.phone_normalized!r}")
    prime_biz = next((b for n, b in by_name.items() if "Prime" in n), None)
    if prime_biz:
        check("99999" in (prime_biz.phone or "") and prime_biz.phone_source == "website",
              "NEW surface: website phone promoted when provider phone empty",
              f"phone={prime_biz.phone!r} src={prime_biz.phone_source!r}")

    # Metrics rolled up onto the job (businesses WITH an email, not raw rows)
    biz_with_email = sum(1 for b in biz if b.email)
    check(job.emails_found == biz_with_email and job.emails_found > 0,
          "job.emails_found == businesses holding an email",
          f"job={job.emails_found} db={biz_with_email}")
    check(job.websites_found == sum(1 for b in biz if b.website_normalized),
          "job.websites_found matches businesses with websites")

    # Requested-vs-available accounting ran (cap hit ⇒ full-delivery wording)
    detail = (job.stage_detail or "").lower()
    check("requested" in detail and "delivered" in detail,
          "requested-vs-available note written", f"detail={job.stage_detail!r}")

    # Dedup ran
    statuses = {b.dedup_status for b in biz}
    check(statuses <= {"unique", "duplicate"}, "dedup assigned only valid statuses",
          f"statuses={statuses}")

# ----------------------------------------------------------------------------
# 5. Real export service: PDF + CSV + XLSX
# ----------------------------------------------------------------------------
print("[5] exports via app.services.export_service.generate_export")
from app.services.export_service import generate_export

from pathlib import Path

out_files: dict[str, Path] = {}
with session_scope() as db:
    job = db.get(SearchJob, job_id)
    for fmt in ("csv", "xlsx", "pdf"):
        filename, rows = generate_export(db, job, fmt, user_id)
        path = Path(settings.pdf_storage_path) / filename
        out_files[fmt] = path
        print(f"  · {fmt}: {filename} rows={rows}")
    csv_text = out_files["csv"].read_text()
    check("Apex" in csv_text and "info@apexclinic-rig.com" in csv_text,
          "CSV contains lead rows incl. crawled email", csv_text[:120])
    check(out_files["xlsx"].stat().st_size > 1000, "XLSX file written", "")
    check(out_files["pdf"].read_bytes()[:5] == b"%PDF-",
          "PDF file starts with %PDF- magic", "")
    exports = db.query(Export).filter(Export.job_id == job_id).count()
    check(exports == 3, "3 Export rows recorded", f"n={exports}")

server.shutdown()
print()
if failures:
    print(f"FAILED ({len(failures)}): " + "; ".join(failures))
    sys.exit(1)
print("ALL E2E BEHAVIOR CHECKS PASSED ✅")
