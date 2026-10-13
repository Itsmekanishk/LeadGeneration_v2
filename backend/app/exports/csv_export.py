"""CSV export builder."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

_COLUMNS = [
    "Business Name", "Category", "Address", "Phone", "Email", "Website",
    "Rating", "Review Count", "Email Confidence", "Phone Source",
    "Website Source", "All Emails", "All Phones",
    "Search Query", "Search Location", "Collected At",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# Signed plain numbers, e.g. an E.164 phone "+919876543210" or "(020) 2665-4321".
# These are data, not formulas, so they must never be apostrophe-prefixed —
# doing so corrupted every phone number in the export.
_NUMERIC_LIKE_RE = re.compile(r"^[+\-]?[\d\s().\-]*\d$")


def _sanitize_cell(value) -> object:
    """Neutralize CSV formula injection (=, +, -, @ prefixes in Excel).

    A cell like ``=HYPERLINK(...)`` would execute when opened in Excel;
    prefixing an apostrophe forces text interpretation. Plain numbers and
    phone numbers are exempt so their value is preserved verbatim.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        if _NUMERIC_LIKE_RE.match(value):
            return value
        return f"'{value}"
    return value


def build_csv(out_path: Path, rows: list[dict]) -> None:
    """Write UTF-8-BOM CSV (Excel-friendly) with the standard column set."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            cleaned = dict(row)
            ts = cleaned.get("Collected At")
            if isinstance(ts, datetime):
                cleaned["Collected At"] = ts.strftime("%Y-%m-%d %H:%M:%S")
            cleaned = {k: _sanitize_cell(v) for k, v in cleaned.items()}
            writer.writerow(cleaned)
