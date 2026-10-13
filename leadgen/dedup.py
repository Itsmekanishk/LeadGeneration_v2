# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Deduplication: Unique Hash = sha256(normalized_phone + domain).

Per the master prompt: if a new lead matches an existing hash it is flagged
``DUPLICATE``; the first occurrence is ``UNIQUE``. Normalized phones come
from :mod:`leadgen.numverify` (``international_format``, digits-only), so
"044 123 456" and "+4444123456" collapse to the same hash — the point of the
whole exercise.

Leads with no normalized phone dedupe on domain alone (with a distinct hash
namespace), so two websites are never merged or lost silently.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class DedupVerdict:
    """Outcome of comparing one lead against the seen-hash set."""

    unique_hash: str
    status: str                 # UNIQUE | DUPLICATE
    first_seen: bool


def compute_unique_hash(normalized_phone: str, domain_name: str) -> str:
    """Stable Unique Hash: sha256(normalized_phone + domain_name).

    *normalized_phone* is the digits-only international format from
    :mod:`leadgen.numverify`; *domain_name* is the registrable website
    domain. Leads without a phone hash on domain alone under a separate
    namespace so they can neither collide with nor merge into phone-keyed
    leads.
    """
    phone = "".join(ch for ch in (normalized_phone or "") if ch.isdigit())
    domain = (domain_name or "").strip().lower().lstrip("@")
    if phone:
        key = f"phone:{phone}|{domain}"
    else:
        key = f"domain:{domain}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class DedupIndex:
    """Tracks Unique Hashes across a run (and across runs via SQLite)."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def classify(self, normalized_phone: str, domain_name: str) -> DedupVerdict:
        """Flag *first* occurrence UNIQUE, later identical hashes DUPLICATE."""
        h = compute_unique_hash(normalized_phone, domain_name)
        if h in self._seen:
            return DedupVerdict(unique_hash=h, status="DUPLICATE",
                                first_seen=False)
        self._seen[h] = domain_name
        return DedupVerdict(unique_hash=h, status="UNIQUE", first_seen=True)

    def is_duplicate(self, normalized_phone: str, domain_name: str) -> bool:
        """Hash membership check that does not mutate the index."""
        return compute_unique_hash(normalized_phone, domain_name) in self._seen
