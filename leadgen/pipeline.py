# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""The pipeline: turns (business name, website, city) into scored leads.

Per lead, in order:

1. **Crawl audit** — fetch the site politely (robots.txt + SSRF guard) and
   record ``http_status``, ``response_time_ms`` and ``robots_allowed``.
2. **SiteIntel** — metadata, emails and social links (Apify actor when
   configured, keyless fallback otherwise).
3. **Disify** — disposable/free-host verdict for each candidate email.
4. **Email Confidence** — HIGH / MEDIUM / LOW tiering.
5. **Numverify** — raw phone → E.164 ``international_format`` (offline
   fallback when unkeyed).
6. **Deduplication** — Unique Hash = sha256(normalized_phone + domain);
   first occurrence UNIQUE, later ones DUPLICATE.
7. **Geocoding** — Nominatim (name + city), REST Countries fallback.

Failures are isolated: one dead website or unreachable API never fails the
run — it degrades that lead's fields and is written to the audit trail.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from leadgen import store
from leadgen.confidence import calculate_email_confidence
from leadgen.config import get_settings
from leadgen.dedup import DedupIndex
from leadgen.disify import check_email
from leadgen.geocode import geocode
from leadgen.numverify import normalize_phone
from leadgen.siteintel import _domain_of, gather_site_intel

log = logging.getLogger("leadgen.pipeline")


@dataclass
class LeadInput:
    """A business to process (website optional; name+city always used)."""

    business_name: str
    website: str = ""
    city: str = ""
    country_code: str = ""
    phone: str = ""
    address: str = ""


@dataclass
class LeadResult:
    """Everything the engine knows about one lead after processing."""

    input: LeadInput
    record: dict[str, Any] = field(default_factory=dict)
    audit_rows: list[dict[str, Any]] = field(default_factory=list)


def process_lead(item: LeadInput, dedup: DedupIndex,
                 conn: Any = None) -> LeadResult:
    """Run the full enrichment chain for one business. Never raises."""
    result = LeadResult(input=item)
    rec: dict[str, Any] = {
        "business_name": item.business_name,
        "website": item.website,
        "domain_name": _domain_of(item.website) if item.website else "",
        "city": item.city,
        "country_code": item.country_code,
        "address": item.address,
    }
    audits: list[dict[str, Any]] = []

    # -- 1+2. SiteIntel: metadata, emails, socials (crawl audited) ---------
    intel = gather_site_intel(item.website) if item.website else None
    if intel:
        audits += intel.audit
        rec["site_title"] = intel.title
        rec["site_description"] = intel.description
        rec["social_links"] = intel.social_links
        rec["emails_found"] = intel.emails
        homepage_ok = any(
            a.get("status_kind") == "ok" for a in intel.audit)
        rec["crawl_http_status"] = next(
            (a.get("http_status") for a in intel.audit
             if a.get("http_status")), None)
        rec["crawl_response_time_ms"] = next(
            (a.get("response_time_ms") for a in intel.audit
             if a.get("response_time_ms")), 0)
        rec["crawl_robots_allowed"] = bool(intel.audit and
                                           intel.audit[0].get(
                                               "robots_allowed", True))
        rec["crawl_summary"] = (
            f"source={intel.source}; homepage_ok={homepage_ok}")
    else:
        rec["crawl_robots_allowed"] = True
        rec["crawl_summary"] = "no website"

    # -- 3+4. Disify + Email Confidence ------------------------------------
    best_email, best_tier = "", "LOW"
    best_disify = None
    tier_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for email in (intel.emails if intel else [])[:10]:
        d = check_email(email)
        audits.append(d.audit)
        tier = calculate_email_confidence(
            email, item.website, is_disposable=d.disposable,
            found_on_website=True, disify=d,
        )
        if tier_rank[tier] < tier_rank[best_tier]:
            best_email, best_tier, best_disify = email, tier, d
    rec["email"] = best_email
    rec["email_confidence"] = best_tier if best_email else ""
    rec["email_reasons"] = [{
        "email": best_email, "tier": best_tier,
        "disposable": best_disify.disposable if best_disify else None,
        "free_host": best_disify.is_free_host if best_disify else None,
        "disify_source": best_disify.source if best_disify else "",
    }] if best_email else []

    # -- 5. Numverify phone normalization -----------------------------------
    phone_res = normalize_phone(item.phone) if item.phone else None
    if phone_res:
        audits.append(phone_res.audit)
        rec["phone_raw"] = item.phone
        rec["phone_normalized"] = phone_res.international_format
        rec["phone_source"] = phone_res.source
        norm_phone = phone_res.international_format
    else:
        norm_phone = ""

    # -- 6. Deduplication: Unique Hash --------------------------------------
    verdict = dedup.classify(norm_phone, rec["domain_name"])
    rec["unique_hash"] = verdict.unique_hash
    rec["dedup_status"] = verdict.status

    # -- 7. Geocoding (Nominatim → REST Countries) --------------------------
    geo = geocode(item.business_name, item.city, item.country_code)
    audits += geo.audit
    rec["latitude"] = geo.latitude
    rec["longitude"] = geo.longitude
    rec["geo_source"] = geo.source
    rec["geo_display_name"] = geo.display_name

    rec["audit_trail"] = audits
    result.record = rec
    result.audit_rows = audits

    # -- Persist (audit trail always lands, even for duplicate leads) -------
    if conn is not None:
        try:
            store.save_lead(conn, rec)
            store.save_audit_events(conn, audits, verdict.unique_hash)
        except Exception:  # noqa: BLE001 — persistence must not kill a run
            log.exception("failed to persist lead %s", item.business_name)
    return result


def run_leads(items: Iterable[LeadInput], *,
              db_path: str | None = None) -> list[LeadResult]:
    """Process many leads with cross-run dedup, politely and audit-logged."""
    s = get_settings()
    conn = store.connect(db_path)
    dedup = DedupIndex()
    # Pre-seed the index with hashes already in the DB so re-runs flag
    # duplicates against previous runs too.
    for h in store.seen_hashes(conn):
        dedup._seen.setdefault(h, "")
    results: list[LeadResult] = []
    try:
        for item in items:
            results.append(process_lead(item, dedup, conn))
            if s.request_delay_seconds > 0:
                import time
                time.sleep(s.request_delay_seconds)
    finally:
        conn.close()
    return results
