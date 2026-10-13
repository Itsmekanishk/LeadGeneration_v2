# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""SQLite persistence (lightweight, open-source; stdlib ``sqlite3``).

Tables:

* ``leads`` — one row per lead, keyed by ``unique_hash`` so dedup persists
  across runs. Carries the email confidence tier, normalized phone, geo
  result and the full crawl audit trail as JSON.
* ``crawl_audit_events`` — one row per audited HTTP call: ``service``,
  ``url``, ``http_status``, ``response_time_ms``, ``robots_allowed``,
  ``status_kind`` — the queryable Crawl Audit Trail.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from leadgen.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    unique_hash     TEXT PRIMARY KEY,
    dedup_status    TEXT NOT NULL DEFAULT 'UNIQUE',
    business_name   TEXT NOT NULL DEFAULT '',
    website         TEXT NOT NULL DEFAULT '',
    domain_name     TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    email_confidence TEXT NOT NULL DEFAULT 'LOW',
    email_reasons   TEXT NOT NULL DEFAULT '[]',
    phone_raw       TEXT NOT NULL DEFAULT '',
    phone_normalized TEXT NOT NULL DEFAULT '',
    phone_source    TEXT NOT NULL DEFAULT '',
    address         TEXT NOT NULL DEFAULT '',
    city            TEXT NOT NULL DEFAULT '',
    country_code    TEXT NOT NULL DEFAULT '',
    latitude        REAL,
    longitude       REAL,
    geo_source      TEXT NOT NULL DEFAULT '',
    geo_display_name TEXT NOT NULL DEFAULT '',
    site_title      TEXT NOT NULL DEFAULT '',
    site_description TEXT NOT NULL DEFAULT '',
    social_links    TEXT NOT NULL DEFAULT '[]',
    emails_found    TEXT NOT NULL DEFAULT '[]',
    crawl_http_status    INTEGER,
    crawl_response_time_ms INTEGER,
    crawl_robots_allowed   INTEGER,
    crawl_pages_scanned    INTEGER NOT NULL DEFAULT 0,
    crawl_summary   TEXT NOT NULL DEFAULT '',
    audit_trail     TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crawl_audit_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_hash        TEXT,
    service          TEXT NOT NULL,
    url              TEXT NOT NULL DEFAULT '',
    http_status      INTEGER,
    response_time_ms INTEGER NOT NULL DEFAULT 0,
    robots_allowed   INTEGER NOT NULL DEFAULT 1,
    status_kind      TEXT NOT NULL DEFAULT '',
    error            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_audit_service
    ON crawl_audit_events (service, created_at);
CREATE INDEX IF NOT EXISTS ix_leads_status
    ON leads (dedup_status);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection with the schema applied (created on first use)."""
    path = Path(db_path or get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def save_lead(conn: sqlite3.Connection, lead: dict[str, Any]) -> None:
    """Insert or update one lead row keyed by its Unique Hash."""
    lead = dict(lead)
    columns = list(lead.keys())
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{c} = excluded.{c}" for c in columns if c != "unique_hash")
    # refresh the update timestamp server-side on every conflict
    if updates:
        updates += ", updated_at = datetime('now')"
    sql = (
        f"INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(unique_hash) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [json.dumps(v, default=str) if isinstance(
        v, (list, dict)) else v for v in lead.values()])
    conn.commit()


def save_audit_events(conn: sqlite3.Connection, rows: list[dict],
                      lead_hash: str | None = None) -> None:
    """Append Crawl Audit Trail rows for one lead (or run-level events)."""
    for r in rows:
        conn.execute(
            "INSERT INTO crawl_audit_events (lead_hash, service, url, "
            "http_status, response_time_ms, robots_allowed, status_kind, "
            "error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_hash, r.get("service", ""), r.get("url", ""),
             r.get("http_status"), r.get("response_time_ms", 0),
             1 if r.get("robots_allowed", True) else 0,
             r.get("status_kind", ""), r.get("error", "")),
        )
    conn.commit()


def seen_hashes(conn: sqlite3.Connection) -> set[str]:
    """All Unique Hashes already stored (for cross-run dedup)."""
    return {row["unique_hash"] for row in
            conn.execute("SELECT unique_hash FROM leads")}


def all_leads(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every stored lead as a plain dict (used by the exporters)."""
    return [dict(row) for row in
            conn.execute("SELECT * FROM leads ORDER BY created_at, domain_name")]
