"""Natural-language query parsing.

Converts free-text like \"Hospitals in Porwal Road Pune\" into a structured
``ParsedQuery(category=\"Hospital\", location_text=\"Porwal Road, Pune\")``.

Design rules:
- Conservative: when we cannot confidently split category vs location we say
  so (``confidence=low``) instead of guessing.
- A curated vocabulary of common industry terms covers the product's target
  verticals; unknown nouns fall back to positional heuristics.
"""
from __future__ import annotations

from typing import Optional

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

# canonical singular -> display label
CATEGORY_VOCAB: dict[str, str] = {
    "hospital": "Hospitals",
    "clinic": "Clinics",
    "dental": "Dental Clinics",
    "dental clinic": "Dental Clinics",
    "dentist": "Dental Clinics",
    "school": "Schools",
    "college": "Colleges",
    "university": "Universities",
    "society": "Societies",
    "housing society": "Societies",
    "hotel": "Hotels",
    "resort": "Resorts",
    "restaurant": "Restaurants",
    "cafe": "Cafes",
    "gym": "Gyms",
    "pharmacy": "Pharmacies",
    "chemist": "Pharmacies",
    "lab": "Diagnostic Labs",
    "pathology lab": "Diagnostic Labs",
    "diagnostic lab": "Diagnostic Labs",
    "salon": "Salons",
    "spa": "Spas",
    "bank": "Banks",
    "atm": "ATMs",
    "petrol pump": "Petrol Pumps",
    "fuel station": "Petrol Pumps",
    "supermarket": "Supermarkets",
    "grocery": "Groceries",
    "bakery": "Bakeries",
    "book shop": "Book Shops",
    "bookstore": "Book Shops",
    "coaching": "Coaching Institutes",
    "coaching institute": "Coaching Institutes",
    "play school": "Play Schools",
    "playschool": "Play Schools",
    "nursery": "Nurseries",
    "coworking": "Coworking Spaces",
    "office": "Offices",
    "builder": "Builders",
    "real estate": "Real Estate Agencies",
    "travel agency": "Travel Agencies",
    "car repair": "Car Repair Shops",
    "garage": "Garages",
}

# Words that frequently appear inside location names but must not be treated
# as the industry term (e.g. \"Garden Road\", \"Hospital Road\").
_AMBIGUOUS_TOKENS = {"road", "street", "nagar", "colony", "park", "garden",
                     "market", "complex", "layout", "hill", "lake", "camp"}

_CITY_HINTS = {
    "pune", "mumbai", "nagpur", "nashik", "nanded", "solapur", "ahmednagar",
    "delhi", "new delhi", "gurgaon", "gurugram", "noida", "bengaluru",
    "bangalore", "hyderabad", "chennai", "kolkata", "ahmedabad", "jaipur",
    "lucknow", "indore", "bhopal", "surat", "vadodara", "kochi", "goa",
}

_LOCATION_PRECEDERS = (
    " in ", " near ", " around ", " at ", " close to ", " within ",
)

_STRIP_SUFFIX = re.compile(
    r"\s*\b(pune|maharashtra|india|city|west|east|north|south)\b\s*$", re.I
)


@dataclass
class ParsedQuery:
    """Result of parsing a raw search string."""

    raw_query: str
    category: str          # display label, e.g. \"Hospitals\"
    category_slug: str     # canonical vocab key, e.g. \"hospital\"
    location_text: str     # e.g. \"Porwal Road, Pune\"
    confidence: str = "high"   # high | medium | low
    notes: list[str] = field(default_factory=list)

    def interpreted(self) -> dict:
        """Compact dict for the frontend 'Interpreted Search' card."""
        return {
            "raw_query": self.raw_query,
            "category": self.category or None,
            "category_slug": self.category_slug or None,
            "location": self.location_text or None,
            "confidence": self.confidence,
            "notes": self.notes,
        }


class ParsedQueryModel(BaseModel):
    """Pydantic mirror of ParsedQuery for API responses."""

    raw_query: str
    category: Optional[str] = None
    category_slug: Optional[str] = None
    location: Optional[str] = None
    confidence: str = "high"
    notes: list[str] = Field(default_factory=list)


def _lookup_vocab(text_lower: str) -> Optional[tuple[str, str]]:
    """Find the longest matching category term in *text_lower*.

    Matching is word-boundary aware and tolerates simple English plurals
    ('hospitals' matches 'hospital', 'societies' matches 'society').
    """
    best: Optional[tuple[str, str]] = None
    best_len = 0
    for term, label in CATEGORY_VOCAB.items():
        pattern = r"\b" + re.escape(term) + r"(?:es|s)?\b"
        if term.endswith("y"):
            # "society" -> "societies"
            pattern += r"|\b" + re.escape(term[:-1]) + r"ies\b"
        if re.search(pattern, text_lower) and len(term) > best_len:
            best = (term, label)
            best_len = len(term)
    return best


def _extract_location(text: str, category_term: Optional[str]) -> Optional[str]:
    """Extract the location phrase after a preceding keyword (\"in\", \"near\")."""
    lowered = f" {text.lower()} "
    if category_term:
        # start searching after the category mention
        idx = lowered.find(category_term)
        if idx >= 0:
            lowered_tail = lowered[idx + len(category_term):]
        else:
            lowered_tail = lowered
    else:
        lowered_tail = lowered

    for prec in _LOCATION_PRECEDERS:
        pos = lowered_tail.find(prec)
        if pos >= 0:
            loc = lowered_tail[pos + len(prec):].strip()
            loc = re.split(r"\s+(?:and|or|near|in)\s+", loc)[0].strip()
            loc = _STRIP_SUFFIX.sub(" ", f" {loc} ").strip()
            # title-case street words conservatively
            loc = ", ".join(p.strip().title() for p in loc.split(",") if p.strip())
            return loc or None
    return None


def _titlecase_location(loc: str) -> str:
    return ", ".join(p.strip().title() for p in loc.split(",") if p.strip())


def parse_query(raw: str) -> ParsedQuery:
    """Parse a raw search string into a structured query.

    Never raises for ordinary input; returns low confidence when ambiguous.
    """
    raw = (raw or "").strip()
    if not raw:
        return ParsedQuery(raw_query="", category="", category_slug="",
                           location_text="", confidence="low",
                           notes=["Empty query"])
    if len(raw) > 300:
        raw = raw[:300]

    lowered = raw.lower()
    match = _lookup_vocab(lowered)

    # --- structured mode: \"category | location\" -------------------------------
    if "|" in raw:
        left, _, right = raw.partition("|")
        term, label = _lookup_vocab(left.lower()) or (left.strip().lower(), left.strip().title())
        return ParsedQuery(
            raw_query=raw,
            category=label,
            category_slug=term,
            location_text=_titlecase_location(right.strip()),
            confidence="high",
            notes=["Structured input (category | location)"],
        )

    notes: list[str] = []
    confidence = "high"

    city = next((c.title() for c in _CITY_HINTS if re.search(rf"\b{re.escape(c)}\b", lowered)), "")

    if match:
        term, label = match
        location = _extract_location(raw, term)
        if location and city and city.lower() not in location.lower():
            location = f"{location}, {city}"
        if not location:
            # maybe the vocab term appears after the location:
            # \"Porwal Road Pune hospitals\"
            notes.append("No location keyword found; using the rest of the query as location.")
            removal = rf"\b{re.escape(term)}(?:es|s)?\b"
            if term.endswith("y"):
                removal += rf"|\b{re.escape(term[:-1])}ies\b"
            location = _titlecase_location(
                re.sub(removal, "", raw, flags=re.I).strip()
            ) or ""
            confidence = "medium"
            location = location or None
        return ParsedQuery(
            raw_query=raw,
            category=label,
            category_slug=term,
            location_text=location or "",
            confidence=confidence,
            notes=notes,
        )

    # --- fallback heuristics -------------------------------------------------
    if any(prec in lowered for prec in _LOCATION_PRECEDERS):
        # \"Xyz things in Some Area\" — treat pre-'in' text as category
        for prec in _LOCATION_PRECEDERS:
            if prec in lowered:
                before, after = lowered.split(prec, 1)
                loc = _extract_location_from_tail(after)
                cat = before.strip().title()
                return ParsedQuery(
                    raw_query=raw,
                    category=cat or "",
                    category_slug=cat.lower() or "",
                    location_text=_titlecase_location(loc or ""),
                    confidence="medium" if loc else "low",
                    notes=["Industry term not in vocabulary; using positional parsing."],
                )

    # last resort: no recognizable structure
    return ParsedQuery(
        raw_query=raw,
        category="",
        category_slug="",
        location_text=_titlecase_location(raw),
        confidence="low",
        notes=[
            "Could not confidently separate industry and location.",
            "Use structured mode (e.g. 'Hospital | Porwal Road, Pune').",
        ],
    )


def _extract_location_from_tail(tail: str) -> Optional[str]:
    """From the text after 'in'/'near', keep everything up to city/suffix words."""
    tail = tail.strip()
    if not tail:
        return None
    m = re.match(r"([\w\s\.\-]+)", tail)
    loc = m.group(1).strip() if m else tail
    loc = _STRIP_SUFFIX.sub(" ", f" {loc} ").strip()
    return loc or None
