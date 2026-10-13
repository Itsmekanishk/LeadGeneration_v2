# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Command-line interface.

Examples::

    # one lead
    python -m leadgen run \
        --name "Sunrise Hospital" --website https://sunrisehospital.in \
        --city Pune --country in --phone "+91 98220 12345"

    # bulk from a CSV with columns business_name,website,city,country_code,phone
    python -m leadgen run --input seeds.csv

    # export the SQLite store to output/leads.json and output/leads.csv
    python -m leadgen export

    # show the Crawl Audit Trail (last N events)
    python -m leadgen audit --limit 20
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from leadgen.config import get_settings
from leadgen.pipeline import LeadInput, run_leads
from leadgen import store
from leadgen.exports import export_all


def _cmd_run(args: argparse.Namespace) -> int:
    get_settings()  # trigger .env load
    items: list[LeadInput] = []
    if args.input:
        with Path(args.input).open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                items.append(LeadInput(
                    business_name=(row.get("business_name") or "").strip(),
                    website=(row.get("website") or "").strip(),
                    city=(row.get("city") or "").strip(),
                    country_code=(row.get("country_code") or "").strip(),
                    phone=(row.get("phone") or "").strip(),
                    address=(row.get("address") or "").strip(),
                ))
        items = [i for i in items if i.business_name or i.website]
    else:
        if not args.name and not args.website:
            print("error: provide --name/--website or --input seeds.csv",
                  file=sys.stderr)
            return 2
        items.append(LeadInput(
            business_name=args.name or "",
            website=args.website or "",
            city=args.city or "",
            country_code=args.country or "",
            phone=args.phone or "",
            address=args.address or "",
        ))

    results = run_leads(items)
    for r in results:
        rec = r.record
        print(f"[{rec.get('dedup_status', '?'):9}] "
              f"{rec.get('business_name') or rec.get('domain_name')!s:<40} "
              f"email={rec.get('email') or '-':<35} "
              f"conf={rec.get('email_confidence') or '-':<6} "
              f"phone={rec.get('phone_normalized') or '-':<18} "
              f"geo={rec.get('geo_source') or '-'}")
    print(f"\n{len(results)} lead(s) processed → {get_settings().db_path}")
    print("export with: python -m leadgen export")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    conn = store.connect(args.db_path)
    try:
        json_path, csv_path = export_all(conn=conn,
                                         output_dir=args.output_dir)
    finally:
        conn.close()
    print(f"wrote {json_path}\nwrote {csv_path}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    conn = store.connect(args.db_path)
    try:
        rows = conn.execute(
            "SELECT created_at, service, status_kind, http_status, "
            "response_time_ms, robots_allowed, url FROM crawl_audit_events "
            "ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
        if not rows:
            print("no audit events yet")
            return 0
        for r in rows:
            print(f"{r['created_at']}  {r['service']:<14} "
                  f"{r['status_kind']:<18} http={r['http_status'] or '-':<4} "
                  f"{r['response_time_ms']:>5}ms  "
                  f"robots={'Y' if r['robots_allowed'] else 'N'}  "
                  f"{r['url'][:80]}")
    finally:
        conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadgen",
        description="Open-source OSINT lead generation engine "
                    "(SiteIntel + Numverify + Disify + Nominatim).")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="process one lead or a CSV of leads")
    run_p.add_argument("--name", help="business name (used for geocoding)")
    run_p.add_argument("--website", help="business website URL")
    run_p.add_argument("--city", help="city (used for geocoding)")
    run_p.add_argument("--country", default="", help="ISO-3166 alpha-2 code")
    run_p.add_argument("--phone", default="", help="raw phone string")
    run_p.add_argument("--address", default="", help="street address")
    run_p.add_argument("--input", help="CSV file with lead columns")
    run_p.set_defaults(func=_cmd_run)

    exp_p = sub.add_parser("export", help="export stored leads to JSON/CSV")
    exp_p.add_argument("--db-path", default=None)
    exp_p.add_argument("--output-dir", default=None)
    exp_p.set_defaults(func=_cmd_export)

    aud_p = sub.add_parser("audit", help="show the Crawl Audit Trail")
    aud_p.add_argument("--limit", type=int, default=25)
    aud_p.add_argument("--db-path", default=None)
    aud_p.set_defaults(func=_cmd_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
