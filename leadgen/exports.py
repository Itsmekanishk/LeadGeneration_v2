# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Export stored leads to structured ``leads.json`` and ``leads.csv``.

CSV columns are flattened (audit trail summarized) so the files open cleanly
in Excel/Sheets; JSON keeps the full nested structure including the complete
audit trail. ``pandas`` is used when available, stdlib ``csv`` otherwise.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from leadgen.config import get_settings
from leadgen.store import all_leads

_JSON_FIELDS = [
    "unique_hash", "dedup_status", "business_name", "website", "domain_name",
    "email", "email_confidence", "email_reasons", "phone_raw",
    "phone_normalized", "phone_source", "address", "city", "country_code",
    "latitude", "longitude", "geo_source", "geo_display_name", "site_title",
    "site_description", "social_links", "emails_found", "crawl_http_status",
    "crawl_response_time_ms", "crawl_robots_allowed", "crawl_pages_scanned",
    "crawl_summary", "audit_trail", "created_at", "updated_at",
]

_CSV_FIELDS = [
    "business_name", "website", "domain_name", "email", "email_confidence",
    "phone_raw", "phone_normalized", "phone_source", "address", "city",
    "country_code", "latitude", "longitude", "geo_source", "dedup_status",
    "unique_hash", "crawl_http_status", "crawl_response_time_ms",
    "crawl_robots_allowed", "crawl_summary", "created_at",
]

# Columns stored as JSON text in SQLite that should be real lists in JSON.
_JSON_LIST_FIELDS = {
    "email_reasons", "social_links", "emails_found", "audit_trail",
}


def _row_for(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields:
        v = row.get(f)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        out[f] = v
    return out


def _row_hydrated(row: dict[str, Any]) -> dict[str, Any]:
    """Row for JSON export: parse TEXT-stored JSON columns back into lists."""
    out: dict[str, Any] = {}
    for f in _JSON_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and f in _JSON_LIST_FIELDS and v.startswith("["):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[f] = v
    return out


def export_json(path: str | Path | None = None,
                conn: Any = None) -> Path:
    """Write every stored lead to ``leads.json`` (full structure)."""
    out = Path(path or (Path(get_settings().output_dir) / "leads.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [_row_hydrated(r) for r in all_leads(conn)] if conn else []
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    return out


def export_csv(path: str | Path | None = None, conn: Any = None) -> Path:
    """Write every stored lead to ``leads.csv`` (flattened columns)."""
    out = Path(path or (Path(get_settings().output_dir) / "leads.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = all_leads(conn) if conn else []
    try:
        import pandas as pd  # type: ignore

        frame = pd.DataFrame([_row_for(r, _CSV_FIELDS) for r in rows],
                             columns=_CSV_FIELDS)
        frame.to_csv(out, index=False)
        return out
    except ImportError:
        pass
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS,
                                extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(_row_for(r, _CSV_FIELDS))
    return out


def export_all(conn: Any = None, output_dir: str | Path | None = None
               ) -> tuple[Path, Path]:
    """Write both export files; returns (json_path, csv_path)."""
    if output_dir:
        j = export_json(Path(output_dir) / "leads.json", conn)
        c = export_csv(Path(output_dir) / "leads.csv", conn)
        return j, c
    return export_json(conn=conn), export_csv(conn=conn)
