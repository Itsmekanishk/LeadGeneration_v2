"""DuplicateResolver: scoring-based deduplication across discovered businesses.

Strong identifiers (near-certain duplicates):
- identical normalized website
- identical normalized phone
- identical provider_place_id (guarded upstream by a unique constraint)

Secondary identifiers (probabilistic):
- normalized business name + normalized address similarity

The resolver marks duplicates in the DB (``dedup_status`` = duplicate|unique,
``duplicate_of_id``, ``dedup_confidence``) and returns a per-group report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from difflib import SequenceMatcher

from sqlalchemy.orm import Session as DBSession

from app.models.models import Business
from app.utils.normalization import normalize_address, normalize_name, normalize_url

STRONG_THRESHOLD = 0.85   # >= this score ⇒ duplicate
REVIEW_THRESHOLD = 0.60   # >= and < strong ⇒ flag for review


@dataclass
class DuplicateGroup:
    """One canonical business and the duplicates merged into it."""

    canonical: Business
    duplicates: list[Business] = field(default_factory=list)
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)


def _name_similarity(a: Business, b: Business) -> float:
    na, nb = normalize_name(a.name), normalize_name(b.name)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _address_similarity(a: Business, b: Business) -> float:
    aa, ab = normalize_address(a.address), normalize_address(b.address)
    if not aa or not ab:
        return 0.0
    if aa == ab:
        return 1.0
    return SequenceMatcher(None, aa, ab).ratio()


def score_pair(a: Business, b: Business) -> tuple[float, list[str]]:
    """Score the likelihood that two rows are the same real-world business.

    Returns (score 0..1, reasons).
    """
    reasons: list[str] = []
    score = 0.0

    wa, wb = a.website_normalized or "", b.website_normalized or ""
    if wa and wb:
        if wa == wb:
            return 1.0, ["identical normalized website"]

    pa, pb = a.phone_normalized or "", b.phone_normalized or ""
    if pa and pb:
        if pa == pb:
            return 1.0, ["identical normalized phone"]

    # proximity: same coordinates within ~50m
    coords = None
    if (a.latitude is not None and b.latitude is not None
            and a.longitude is not None and b.longitude is not None):
        dist_ok = abs(a.latitude - b.latitude) < 0.0005 and \
            abs(a.longitude - b.longitude) < 0.0005
        if dist_ok:
            coords = 0.35

    name_sim = _name_similarity(a, b)
    addr_sim = _address_similarity(a, b)

    if name_sim >= 0.92:
        score += 0.45
        reasons.append(f"very similar names ({name_sim:.2f})")
    elif name_sim >= 0.75:
        score += 0.25
        reasons.append(f"similar names ({name_sim:.2f})")

    if addr_sim >= 0.85:
        score += 0.35
        reasons.append(f"very similar addresses ({addr_sim:.2f})")
    elif addr_sim >= 0.6:
        score += 0.2
        reasons.append(f"similar addresses ({addr_sim:.2f})")

    if coords:
        score += coords
        reasons.append("same coordinates (±50m)")

    if a.category and b.category and a.category.lower() == b.category.lower():
        score += 0.05

    return min(score, 1.0), reasons


class DuplicateResolver:
    """Dedup engine for one job's businesses."""

    def __init__(self, db: DBSession) -> None:
        self._db = db

    def resolve_job(self, job_id: str) -> list[DuplicateGroup]:
        """Dedup all businesses of a job; persists dedup metadata.

        Rows with identical normalized websites/phones become duplicates even
        if names differ slightly; name+address pairs are matched fuzzily.
        The first row (by provider order) becomes the canonical record.
        """
        businesses = (
            self._db.query(Business)
            .filter(Business.job_id == job_id)
            .order_by(Business.created_at.asc())
            .all()
        )
        for b in businesses:
            b.dedup_status = "unique"
            b.duplicate_of_id = None
            b.dedup_confidence = 0.0

        n = len(businesses)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int, score: float) -> None:
            ri, rj = find(i), find(j)
            if ri == rj:
                return
            # attach smaller index (earlier row) as canonical root
            if ri < rj:
                parent[rj] = ri
            else:
                parent[ri] = rj

        scores: dict[tuple[int, int], tuple[float, list[str]]] = {}
        for i in range(n):
            for j in range(i + 1, n):
                score, reasons = score_pair(businesses[i], businesses[j])
                if score >= STRONG_THRESHOLD:
                    union(i, j, score)
                    scores[(i, j)] = (score, reasons)

        groups: dict[int, list[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)

        out: list[DuplicateGroup] = []
        for root, members in groups.items():
            if len(members) == 1:
                continue
            members.sort()
            canonical = businesses[members[0]]
            group = DuplicateGroup(
                canonical=canonical,
                duplicates=[businesses[m] for m in members[1:]],
                confidence=max(scores.get((min(members), m), (0.0, []))[0]
                               for m in members[1:]) if len(members) > 1 else 0.0,
                reasons=scores.get((members[0], members[1]), (0.0, []))[1],
            )
            for m in members[1:]:
                b = businesses[m]
                b.dedup_status = "duplicate"
                b.duplicate_of_id = canonical.id
                b.dedup_confidence = scores.get((min(members), m), (0.0, []))[0]
            canonical.dedup_confidence = max(
                canonical.dedup_confidence,
                max((scores.get((min(members), m), (0.0, []))[0] for m in members[1:]),
                    default=0.0),
            )
            out.append(group)

        self._db.commit()
        return out
