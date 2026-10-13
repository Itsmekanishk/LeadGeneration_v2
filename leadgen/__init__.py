# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""leadgen: an open-source lead generation engine.

Extracts business contact data from public websites, validates it through a
multi-tier pipeline (Numverify phones, Disify disposable-mail checks,
SiteIntel website metadata), geocodes locations with OpenStreetMap
(Nominatim + REST Countries fallback — no Google Maps endpoints anywhere),
and stores/exported results to SQLite, JSON and CSV.

Every external request goes through :mod:`leadgen.audit` first, which
records ``http_status``, ``response_time_ms`` and a ``robots_allowed``
boolean — the *Crawl Audit Trail*.
"""
from __future__ import annotations

__version__ = "1.0.0"
__license__ = "MIT"

__all__ = [
    "__version__",
    "__license__",
]
