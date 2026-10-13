"""Query parser tests (Phase 16)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.query_parser import parse_query


def test_hospital_search():
    parsed = parse_query("Hospitals in Porwal Road Pune")
    assert parsed.category == "Hospitals"
    assert "Porwal Road" in parsed.location_text
    assert "Pune" in parsed.location_text
    assert parsed.confidence == "high"


def test_schools_near():
    parsed = parse_query("Schools near Kharadi Pune")
    assert parsed.category == "Schools"
    assert "Kharadi" in parsed.location_text


def test_societies():
    parsed = parse_query("Societies in Porwal Road Pune")
    assert parsed.category == "Societies"
    assert "Porwal Road" in parsed.location_text


def test_dental_clinics():
    parsed = parse_query("Dental clinics in Kharadi Pune")
    assert parsed.category == "Dental Clinics"
    assert "Kharadi" in parsed.location_text


def test_hotels_near_hinjewadi():
    parsed = parse_query("Hotels near Hinjewadi Pune")
    assert parsed.category == "Hotels"
    assert "Hinjewadi" in parsed.location_text


def test_structured_mode():
    parsed = parse_query("Hospital | Viman Nagar, Pune")
    assert parsed.category == "Hospitals"
    assert parsed.location_text == "Viman Nagar, Pune"
    assert parsed.confidence == "high"


def test_unknown_category_is_low_confidence():
    parsed = parse_query("asdasd xyz")
    assert parsed.confidence in ("medium", "low")
    assert parsed.notes


def test_empty_query():
    parsed = parse_query("")
    assert parsed.confidence == "low"


def test_interpreted_dict_shape():
    parsed = parse_query("Gyms in Baner Pune")
    data = parsed.interpreted()
    assert {"raw_query", "category", "location", "confidence", "notes"} <= set(data)
