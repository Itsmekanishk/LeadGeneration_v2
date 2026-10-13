"""Generate example reports (PDF/CSV/XLSX) into ../examples.

Run: ./.venv/bin/python scripts/generate_examples.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

EXAMPLES = BACKEND.parent / "examples"


class DemoBusiness:
    """Duck-typed stand-in for the Business ORM model."""

    def __init__(self, i: int) -> None:
        self.name = f"Demo Business {i}"
        self.category = "Hospitals"
        self.address = f"{100 + i} Demo Street, Porwal Road, Pune 4110{i:02d}"
        self.phone = "+91 98765 43210"
        self.phone_normalized = "+919876543210"
        self.phone_source = "provider"
        self.email = f"info{i}@demobusiness{i % 3}.example" if i % 2 else ""
        self.email_confidence = "HIGH" if i % 4 == 1 else ("MEDIUM" if i % 2 else "")
        self.website = f"https://www.demobusiness{i % 3}.example" if i % 4 else ""
        self.website_normalized = self.website.removeprefix("https://www.")
        self.rating = round(3.6 + (i % 14) / 10.0, 1)
        self.review_count = 20 + i * 13
        self.source = "mock (Google Places API format)"


def main() -> int:
    from app.exports.csv_export import build_csv
    from app.exports.pdf_report import build_pdf_report
    from app.exports.xlsx_export import build_xlsx

    EXAMPLES.mkdir(parents=True, exist_ok=True)
    businesses = [DemoBusiness(i) for i in range(1, 26)]

    rows = [{
        "Business Name": b.name, "Category": b.category, "Address": b.address,
        "Phone": b.phone_normalized, "Email": b.email, "Website": b.website,
        "Rating": b.rating, "Review Count": b.review_count,
        "Email Confidence": b.email_confidence, "Phone Source": b.phone_source,
        "Website Source": b.source, "Search Query": "Hospitals in Porwal Road Pune",
        "Search Location": "Porwal Road, Pune", "Collected At": "2026-09-16 10:00:00",
    } for b in businesses]

    pdf_path = EXAMPLES / "example_report_hospitals_porwal_road_pune.pdf"
    build_pdf_report(pdf_path, search_query="Hospitals in Porwal Road Pune",
                     category="Hospitals", location="Porwal Road, Pune",
                     businesses=businesses, provider="mock")
    build_csv(EXAMPLES / "example_report_hospitals_porwal_road_pune.csv", rows)
    build_xlsx(EXAMPLES / "example_report_hospitals_porwal_road_pune.xlsx", rows)

    for p in sorted(EXAMPLES.glob("example_report_*")):
        print(f"{p.name}  ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
