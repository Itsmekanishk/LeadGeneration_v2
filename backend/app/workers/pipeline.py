"""Search pipeline: the end-to-end flow of one search job.

discover → enrich → crawl → extract → dedup → complete

Every stage updates job progress (DB + Redis pub/sub). Per-business failures
(timeout, HTTP errors, dead sites) are isolated: they mark that business's
website scan as failed and the pipeline continues.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.crawlers.contact_finder import crawl_business_website
from app.models.models import Business, BusinessContact, CrawlPage, SearchJob
from app.providers.base import (
    PlaceDetails,
    PlaceSearchQuery,
    ProviderError,
    friendly_provider_message,
)
from app.providers.factory import search_with_fallback
from app.services.contact_normalizer import email_priority, normalize_phone
from app.services.deduplication import DuplicateResolver
from app.services.osint_finder import resolve_business_web_presence
from app.services.query_parser import parse_query
from app.utils.datetime_utils import utcnow
from app.utils.normalization import clean_category, normalize_url
from app.workers.progress import update_job

log = event_logger("locallead.pipeline")


def _job_cancelled(db: DBSession, job_id: str) -> bool:
    """Re-read the job status from the DB (past update_job commits)."""
    db.expire_all()
    job = db.get(SearchJob, job_id)
    return bool(job and job.status == "cancelled")


def _persist_business(db: DBSession, job: SearchJob, place: PlaceDetails) -> Business:
    """Insert one normalized business (idempotent per provider_place_id)."""
    existing = db.query(Business).filter(
        Business.job_id == job.id,
        Business.provider_place_id == place.provider_place_id,
    ).first()
    if existing:
        return existing
    phone_norm, _cc = normalize_phone(place.phone)
    biz = Business(
        job_id=job.id,
        provider=place.provider,
        provider_place_id=place.provider_place_id,
        name=place.business_name,
        category=clean_category(place.category),
        address=place.address,
        phone=place.phone,
        phone_normalized=phone_norm,
        phone_source="provider" if place.phone else "",
        website=place.website,
        website_normalized=normalize_url(place.website),
        rating=place.rating,
        review_count=place.review_count,
        latitude=place.latitude,
        longitude=place.longitude,
        maps_url=place.maps_url,
        opening_hours=place.opening_hours,
        source=({"mock": "mock provider", "serper": "Serper.dev",
                 "nominatim": "OpenStreetMap (Nominatim)",
                 "geoapify": "Geoapify Places"}.get(
                     place.provider, f"{place.provider} (Google Places API)")),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(biz)
    db.flush()
    if place.phone:
        db.add(BusinessContact(
            business_id=biz.id,
            phone=place.phone,
            phone_normalized=phone_norm,
            source="provider",
            confidence="HIGH",
        ))
    if place.email:
        biz.email = place.email
        biz.email_confidence = "HIGH"
        db.add(BusinessContact(
            business_id=biz.id,
            email=place.email,
            source="provider",
            confidence="HIGH",
        ))
    db.flush()
    return biz


def _scan_business_website(business_id: str, website: str, domain: str,
                           country: str = "IN") -> dict:
    """Run the website crawl in the worker thread; returns a plain dict."""
    outcome = crawl_business_website(
        website, business_domain=domain, default_country=country
    )
    return {
        "business_id": business_id,
        "pages": [
            {
                "url": p.url, "page_type": p.page_type, "status": p.status,
                "http_status": p.http_status, "emails": p.emails_found,
                "phones": p.phones_found, "duration_ms": p.duration_ms,
            }
            for p in outcome.pages
        ],
        "emails": [
            {"email": e.email, "source_url": e.source_url,
             "confidence": e.confidence, "context": e.context,
             "disposable": e.disposable}
            for e in outcome.emails
        ],
        "phones": [
            {"raw": f.raw, "confidence": f.confidence,
             "source_url": f.source_url}
            for f in outcome.phone_findings
        ],
        "ok": outcome.ok,
        "summary": outcome.summary,
    }


_CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# Provenance values the OSINT stage stamps on discovered contacts. A phone or
# email from one of these is a best-effort web guess, so a contact the
# business's own website confirms should replace it.
_OSINT_SOURCES = {
    "osint", "cache", "yahoo_web", "ddg_web", "osm_extratags",
    "domain_probe", "serper",
}


def _apply_scan_results(db: DBSession, business: Business, scan: dict,
                        default_country: str = "IN") -> None:
    """Persist crawl pages, emails, phones from one website scan.

    Contacts are merged with rows already persisted for the business (e.g. the
    provider-supplied phone/email) so nothing is stored twice, while every
    *distinct* email and phone found is kept — a business with a reception and
    an appointments number yields both.
    """
    seen_urls = set()
    for page in scan.get("pages", []):
        url = page["url"][:1000]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        db.add(CrawlPage(
            business_id=business.id,
            url=url,
            page_type=page.get("page_type", "other"),
            http_status=page.get("http_status"),
            status=page.get("status", "error"),
            emails_found=page.get("emails", 0),
            phones_found=page.get("phones", 0),
            duration_ms=page.get("duration_ms", 0),
        ))

    existing_contacts = (
        db.query(BusinessContact)
        .filter(BusinessContact.business_id == business.id)
        .all()
    )
    contact_by_email = {
        c.email.strip().lower(): c for c in existing_contacts if c.email
    }
    # Every number already on record for this lead, so a repeat finding from a
    # second page is not stored again.
    stored_phones = set()
    for contact in existing_contacts:
        value = (contact.phone_normalized or contact.phone or "").strip()
        if value:
            stored_phones.add(value)
    primary_phone = (business.phone_normalized or business.phone or "").strip()
    if primary_phone:
        stored_phones.add(primary_phone)

    # Did the current primary email come from OSINT (a web guess)? If so, a
    # contact the business's own website confirms should be allowed to replace
    # it even at equal confidence.
    current_email_key = (business.email or "").strip().lower()
    osint_email = bool(current_email_key) and any(
        (c.email or "").strip().lower() == current_email_key
        and (c.source or "") in _OSINT_SOURCES
        for c in existing_contacts
    )

    best_email = None
    for email in scan.get("emails", []):
        addr = (email.get("email") or "").strip()
        if not addr:
            continue
        conf = email.get("confidence", "LOW")
        key = addr.lower()
        row = contact_by_email.get(key)
        if row is None:
            row = BusinessContact(
                business_id=business.id,
                email=addr,
                source_url=email.get("source_url", "")[:1000],
                source="website",
                confidence=conf,
                email_disposable=email.get("disposable"),
            )
            db.add(row)
            contact_by_email[key] = row
        else:
            # Same address already recorded (provider row or a prior page):
            # merge into it instead of storing a second row for one mailbox.
            if _CONFIDENCE_ORDER.get(conf, 9) < _CONFIDENCE_ORDER.get(row.confidence, 9):
                row.confidence = conf
            if row.email_disposable is None and email.get("disposable") is not None:
                row.email_disposable = bool(email["disposable"])
        # Prefer higher confidence, then a monitored role mailbox (info@…)
        # over a personal/generic address.
        if best_email is None or (
            _CONFIDENCE_ORDER.get(conf, 9), email_priority(addr)
        ) < (
            _CONFIDENCE_ORDER.get(best_email[1], 9), email_priority(best_email[0])
        ):
            best_email = (addr, conf)

    # --- phones: best-first findings carry their own confidence/provenance.
    website_phone: Optional[dict] = None
    seen_phone_norms = set()
    first_page_url = (scan.get("pages") or [{}])[0].get("url", "")
    for finding in scan.get("phones", []):
        raw = (finding.get("raw") or "").strip()
        if not raw:
            continue
        conf = finding.get("confidence", "MEDIUM")
        src_url = finding.get("source_url") or first_page_url
        # The crawler normalized with the same region; recomputing here keeps
        # this function the single authority on what gets stored.
        norm, _cc = normalize_phone(raw, default_country)
        if not norm or norm in seen_phone_norms:
            continue
        seen_phone_norms.add(norm)
        if website_phone is None:
            website_phone = {"raw": raw, "normalized": norm, "confidence": conf}
        if norm in stored_phones:
            continue
        stored_phones.add(norm)
        try:
            with db.begin_nested():
                db.add(BusinessContact(
                    business_id=business.id,
                    phone=raw,
                    phone_normalized=norm,
                    source_url=src_url[:1000],
                    source="website",
                    confidence=conf,
                ))
        except Exception as exc:  # noqa: BLE001
            # A database that has not run the latest migration still carries the
            # old (business_id, email) unique constraint, which allows a single
            # phone-only row. Contained in a savepoint so one extra number can
            # never abort the job; the lead's primary phone is unaffected.
            log.warning("extra_phone_not_stored", business_id=business.id,
                        phone=norm, error=str(exc)[:120])

    if best_email:
        addr, conf = best_email
        current_conf = business.email_confidence or ""
        # Never downgrade a stronger existing email, but DO let the business's
        # own website supersede an equal-confidence OSINT guess.
        should_set = (
            not business.email
            or _CONFIDENCE_ORDER.get(conf, 9) < _CONFIDENCE_ORDER.get(current_conf, 9)
            or (osint_email and _CONFIDENCE_ORDER.get(conf, 9) <= _CONFIDENCE_ORDER.get(current_conf, 9))
        )
        if should_set:
            business.email = addr
            business.email_confidence = conf
    if website_phone:
        # The business's own site is more authoritative than a scraped search
        # snippet, so it also replaces an OSINT-sourced number of any tier.
        osint_phone = business.phone_source in _OSINT_SOURCES
        if not business.phone or osint_phone:
            business.phone = website_phone["raw"]
            business.phone_normalized = website_phone["normalized"]
            business.phone_source = "website"


def run_pipeline(db: DBSession, job_id: str) -> None:
    """Execute the full search pipeline for *job_id*.

    Never raises for per-business failures; raises only for fatal,
    job-level failures (provider outage, DB loss) which mark the job failed.
    """
    settings = get_settings()
    job = db.get(SearchJob, job_id)
    if job is None:
        log.error("pipeline_job_missing", job_id=job_id)
        return

    try:
        # ------------------------------------------------------------------
        # Stage 1: discovery
        # ------------------------------------------------------------------
        parsed = parse_query(job.query_raw)
        update_job(db, job_id, status="discovering",
                   stage_detail=f"Searching {parsed.category or 'businesses'} in {parsed.location_text or job.parsed_location}")

        # Resolve missing coordinates via Nominatim (OpenStreetMap) so every
        # provider search starts from a real geographic center. Opt-in via
        # ENABLE_NOMINATIM_GEOCODE, and skipped for mock (offline test rig).
        job_lat, job_lon = job.parsed_latitude, job.parsed_longitude
        requested_provider = job.provider or settings.business_provider
        # Region used to normalize country-less phone numbers found on the web.
        country = job.country or settings.default_country or "in"
        if (job_lat is None or job_lon is None) and \
                settings.enable_nominatim_geocode and \
                requested_provider != "mock":
            from app.providers.nominatim import resolve_location

            location_text = job.parsed_location or parsed.location_text or ""
            resolved = resolve_location(location_text,
                                        country=job.country or "")
            if resolved:
                job_lat, job_lon = resolved[0], resolved[1]
                update_job(db, job_id, stage_detail=(
                    f"Location resolved via OpenStreetMap: "
                    f"{resolved[2][:120]}"))

        query = PlaceSearchQuery(
            category=(parsed.category_slug or parsed.category or "business"),
            location_text=job.parsed_location or parsed.location_text or job.query_raw,
            latitude=job_lat,
            longitude=job_lon,
            radius_m=job.radius_m or settings.default_radius_m,
            max_results=job.max_results,
            country=job.country or settings.default_country,
            language=job.language or settings.default_language,
        )
        # Side-by-side providers: run discovery on the job's provider, falling
        # back down the configured chain (PROVIDER_FALLBACK_CHAIN) if it fails.
        fallback_note = ""
        try:
            places, used_provider, fallback_error = search_with_fallback(
                requested_provider,
                query,
                user_id=job.user_id,
                job_id=job.id,
            )
        except ProviderError as exc:
            # No configured provider could be attempted at all.
            log.error("provider_search_failed", job_id=job_id, error=str(exc)[:500])
            update_job(db, job_id, status="failed",
                       stage_detail=friendly_provider_message(exc),
                       error_message=str(exc)[:1000])
            return

        if used_provider != requested_provider:
            # A fallback provider served this job — say so on the job record.
            fallback_note = (
                f"Primary provider '{requested_provider}' unavailable — "
                f"results served by fallback '{used_provider}'."
            )
            update_job(db, job_id, provider=used_provider,
                       stage_detail=fallback_note)

        if fallback_error is not None and not places:
            # Every provider in the chain failed — mark the job failed.
            log.error("provider_chain_exhausted", job_id=job_id,
                      error=str(fallback_error)[:500])
            update_job(db, job_id, status="failed",
                       stage_detail=friendly_provider_message(fallback_error),
                       error_message=str(fallback_error)[:1000])
            return

        if _job_cancelled(db, job_id):
            log.info("pipeline_cancelled_after_discovery", job_id=job_id)
            return
        update_job(db, job_id, total=len(places),
                   stage_detail=f"{len(places)} businesses found")

        if not places:
            update_job(db, job_id, status="completed",
                       stage_detail="No results", total=0)
            return

        # ------------------------------------------------------------------
        # Stage 2: normalize + persist businesses
        # ------------------------------------------------------------------
        update_job(db, job_id, status="enriching", stage_detail="Normalizing records")
        businesses: list[Business] = []
        for place in places:
            if not place.provider_place_id and not place.business_name:
                continue  # malformed record with no usable identity — skip it
            try:
                # Savepoint isolation: one bad record cannot sink the whole job.
                with db.begin_nested():
                    biz = _persist_business(db, job, place)
                businesses.append(biz)
            except Exception as exc:  # noqa: BLE001 — per-record isolation
                log.error(
                    "business_persist_failed",
                    job_id=job_id,
                    place_id=place.provider_place_id[:80],
                    error=str(exc)[:200],
                )
        db.commit()
        update_job(db, job_id, progress_set=35)

        # ------------------------------------------------------------------
        # Stage 2.5: OSINT web presence & contact discovery
        # ------------------------------------------------------------------
        # Resolves websites and contacts for businesses with missing information
        # using domain probing, Serper, and public web intelligence.
        missing_biz = [b for b in businesses if not b.website_normalized or not b.phone]
        if missing_biz and settings.enable_osint_resolution and requested_provider != "mock":
            update_job(
                db, job_id, status="enriching",
                stage_detail=f"Resolving web presence for {len(missing_biz)} places",
            )
            loc = job.parsed_location or parsed.location_text or ""

            def _resolve_one(biz_id: str, name: str) -> tuple[str, Any]:
                try:
                    res = resolve_business_web_presence(name, loc, country)
                    return biz_id, res
                except Exception as exc:  # noqa: BLE001
                    log.warning("osint_resolve_failed", biz_id=biz_id, biz_name=name, error=str(exc)[:150])
                    return biz_id, None

            with ThreadPoolExecutor(max_workers=min(len(missing_biz), 4)) as pool:
                res_futures = [
                    pool.submit(_resolve_one, b.id, b.name)
                    for b in missing_biz
                ]
                biz_map = {b.id: b for b in businesses}
                for f in as_completed(res_futures):
                    if _job_cancelled(db, job_id):
                        log.info("pipeline_cancelled_during_osint", job_id=job_id)
                        return
                    bid, osint_res = f.result()
                    if not osint_res:
                        continue
                    b = biz_map.get(bid)
                    if not b:
                        continue

                    # Update website if missing
                    if osint_res.website and not b.website_normalized:
                        b.website = osint_res.website
                        b.website_normalized = normalize_url(osint_res.website)
                    elif not b.website_normalized and osint_res.social_urls:
                        b.website = osint_res.social_urls[0]
                        b.website_normalized = normalize_url(osint_res.social_urls[0])

                    # Record every number OSINT surfaced, not just the first;
                    # the first also becomes the lead's headline phone.
                    for raw_ph in osint_res.phones or []:
                        norm_ph, _cc = normalize_phone(raw_ph, default_country=country)
                        if not norm_ph:
                            continue
                        if not b.phone:
                            b.phone = raw_ph
                            b.phone_normalized = norm_ph
                            b.phone_source = osint_res.source or "osint"
                        try:
                            with db.begin_nested():
                                already = db.query(BusinessContact).filter(
                                    BusinessContact.business_id == b.id,
                                    BusinessContact.phone_normalized == norm_ph,
                                ).first()
                                if already is None:
                                    db.add(BusinessContact(
                                        business_id=b.id,
                                        phone=raw_ph,
                                        phone_normalized=norm_ph,
                                        source="osint",
                                        confidence="HIGH",
                                    ))
                        except Exception:
                            pass

                    # Update email if missing
                    if osint_res.emails and not b.email:
                        cand_email = osint_res.emails[0]
                        b.email = cand_email
                        b.email_confidence = "MEDIUM"
                        try:
                            with db.begin_nested():
                                existing_em = db.query(BusinessContact).filter(
                                    BusinessContact.business_id == b.id,
                                    BusinessContact.email == cand_email,
                                ).first()
                                if not existing_em:
                                    db.add(BusinessContact(
                                        business_id=b.id,
                                        email=cand_email,
                                        source="osint",
                                        confidence="MEDIUM",
                                    ))
                        except Exception:
                            pass

            db.commit()
            update_job(
                db, job_id, progress_set=45,
                websites_found=sum(1 for b in businesses if b.website_normalized),
                phones_found=sum(1 for b in businesses if b.phone_normalized),
            )

        # ------------------------------------------------------------------
        # Stage 3+4: website crawling + extraction
        # ------------------------------------------------------------------
        scan_results: dict[str, dict] = {}
        if settings.enable_website_crawling:
            update_job(db, job_id, status="crawling",
                       stage_detail="Scanning websites",
                       websites_found=sum(1 for b in businesses if b.website_normalized))
            targets = [
                (b.id, b.website_normalized)
                for b in businesses
                if b.website_normalized
            ]
            with ThreadPoolExecutor(max_workers=settings.crawl_job_concurrency) as pool:
                futures = {
                    pool.submit(_scan_business_website, bid, url, url, country): bid
                    for bid, url in targets
                }
                done = 0
                for future in as_completed(futures):
                    if _job_cancelled(db, job_id):
                        log.info("pipeline_cancelled_during_crawl", job_id=job_id)
                        return
                    bid = futures[future]
                    done += 1
                    try:
                        scan = future.result()
                        scan_results[bid] = scan
                    except Exception as exc:  # noqa: BLE001 — isolate failures
                        log.error("website_scan_failed", business_id=bid,
                                  error=str(exc)[:300])
                        scan_results[bid] = {
                            "business_id": bid, "pages": [], "emails": [],
                            "phones": [], "ok": False,
                            "summary": f"scan error: {exc}"[:200],
                        }
                    progress = 45 + int(40 * done / max(len(targets), 1))
                    update_job(db, job_id, progress_set=progress,
                               stage_detail=f"Scanning {done}/{len(targets)} websites",
                               completed=done)
        else:
            update_job(db, job_id, progress_set=85)

        # ------------------------------------------------------------------
        # Stage 4.5: requested vs. available accounting (honest shortfall notice)
        # ------------------------------------------------------------------
        requested = job.requested_results or job.max_results
        effective_max = job.max_results
        delivered = len(businesses)
        if delivered < effective_max:
            shortfall_note = (
                f"Requested {requested}, showing {delivered} available"
                f" — the data source returned fewer matches for this query."
            )
            log.info(
                "requested_results_shortfall",
                job_id=job_id, requested=requested, delivered=delivered,
            )
        elif effective_max < requested:
            shortfall_note = (
                f"Requested {requested}; platform limit is {effective_max} per "
                f"search — showing all {delivered} available results."
            )
        else:
            shortfall_note = f"All {requested} requested results delivered."

        # ------------------------------------------------------------------
        # Stage 5: persist contacts + crawl pages
        # ------------------------------------------------------------------
        update_job(db, job_id, status="extracting", stage_detail="Extracting contacts")
        emails_found = 0
        phones_found = 0
        for biz in businesses:
            scan = scan_results.get(biz.id)
            if scan:
                # Savepoint isolation: one business's contact rows failing
                # must not abort the whole extraction stage.
                try:
                    with db.begin_nested():
                        _apply_scan_results(db, biz, scan, country)
                except Exception as exc:  # noqa: BLE001 — per-record isolation
                    log.error("contact_persist_failed", business_id=biz.id,
                              error=str(exc)[:200])
            if getattr(biz, "email", None):
                emails_found += 1
            if biz.phone_normalized:
                phones_found += 1
        db.commit()

        websites_found = sum(1 for b in businesses if b.website_normalized)
        update_job(db, job_id, emails_found=emails_found,
                   phones_found=phones_found, websites_found=websites_found,
                   progress_set=90)

        # ------------------------------------------------------------------
        # Stage 6: deduplication
        # ------------------------------------------------------------------
        update_job(db, job_id, status="deduplicating", stage_detail="Resolving duplicates")
        resolver = DuplicateResolver(db)
        groups = resolver.resolve_job(job_id)
        log.info("dedup_completed", job_id=job_id, groups=len(groups))

        # ------------------------------------------------------------------
        # Done
        # ------------------------------------------------------------------
        update_job(
            db, job_id,
            status="completed",
            # Keep the fallback note visible on the finished job (≤255 chars).
            stage_detail=(f"{fallback_note} {shortfall_note}").strip()[:250],
            completed=len(businesses),
            failed=sum(
                1 for b in businesses
                if any(
                    page.get("status") not in ("ok", "robots_disallowed")
                    for page in (scan_results.get(b.id, {}).get("pages") or [])
                )
            ),
            progress_set=100,
        )
    except Exception as exc:  # noqa: BLE001 — fatal job-level failure
        log.error("pipeline_fatal", job_id=job_id, error=str(exc)[:500])
        db.rollback()
        try:
            update_job(db, job_id, status="failed",
                       stage_detail="Unexpected error",
                       error_message=str(exc)[:1000])
        except Exception:  # noqa: BLE001
            log.error("pipeline_failure_report_failed", job_id=job_id)
        raise
