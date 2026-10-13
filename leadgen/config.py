# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Configuration: every credential and endpoint enters the engine here.

Secrets are read from a ``.env`` file (gitignored) or the process
environment — never hardcoded. The engine degrades gracefully: without an
``APIFY_TOKEN`` SiteIntel enrichment is skipped, without ``NUMVERIFY_API_KEY``
phone normalization falls back to the ``phonenumbers`` library / regex rules,
and without any keys the audit, Disify (keyless) and Nominatim layers still
work, so the whole engine is usable at zero cost.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path | None = None) -> dict[str, str]:
    """Load KEY=VALUE pairs from *path* (default: ``.env`` near the repo root).

    Existing environment variables always win over the file. Returns the
    parsed mapping (empty when the file is missing). Implemented locally so
    the engine has no dependency on python-dotenv.
    """
    if path is None and os.getenv("PYTEST_CURRENT_TEST"):
        return {}
    candidates = [Path(path)] if path else [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        parsed: dict[str, str] = {}
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                parsed[key] = value
        for key, value in parsed.items():
            os.environ.setdefault(key, value)
        return parsed
    return {}


@dataclass
class Settings:
    """Engine settings; construct via :func:`get_settings` to pick up ``.env``."""

    # --- SiteIntel (Apify actor: metadata, emails, social links) ---
    apify_token: str = ""
    siteintel_actor: str = "delightful_unicorn~siteintel-domain-api"

    # --- Numverify (E.164 phone normalization) ---
    numverify_api_key: str = ""
    # Numverify's free plan only serves plain http; set NUMVERIFY_HTTPS=true
    # for paid plans that support TLS.
    numverify_https: bool = False

    # --- Disify (keyless disposable/free-mail detection) ---
    disify_base_url: str = "https://disify.com/api"

    # --- Geocoding (OpenStreetMap — replaces all Google Maps endpoints) ---
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    # Nominatim's usage policy requires an honest, descriptive User-Agent
    # identifying the client. Placeholder contact emails (admin@example.com)
    # are actively blocked with HTTP 403 by the OSMF endpoint — set a real
    # project URL or contact via LEADGEN_USER_AGENT when deploying.
    user_agent: str = (
        "leadgen-engine/1.0 (open-source lead generation; "
        "https://github.com/locallead/leadgen)"
    )

    # --- Storage / output ---
    db_path: str = "leadgen.db"
    output_dir: str = "output"

    # --- Politeness / robustness ---
    timeout_seconds: int = 15
    max_retries: int = 2
    request_delay_seconds: float = 1.0  # >= 1 req/s against third-party APIs

    extra_env: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def numverify_base_url(self) -> str:
        scheme = "https" if self.numverify_https else "http"
        return f"{scheme}://apilayer.net/api/validate"


_settings: Settings | None = None


def get_settings(path: str | Path | None = None, refresh: bool = False) -> Settings:
    """Return process-wide settings, loading ``.env`` on first use."""
    global _settings
    if _settings is None or refresh:
        extra = load_dotenv(path)
        s = Settings(
            apify_token=os.getenv("APIFY_TOKEN", ""),
            siteintel_actor=os.getenv("SITEINTEL_ACTOR", Settings.siteintel_actor),
            numverify_api_key=(os.getenv("NUMVERIFY_API_KEY", "")
                               or os.getenv("APILAYER_API_KEY", "")),
            numverify_https=os.getenv("NUMVERIFY_HTTPS", "").lower() in
            {"1", "true", "yes"},
            disify_base_url=os.getenv(
                "DISIFY_BASE_URL", Settings.disify_base_url),
            nominatim_base_url=os.getenv(
                "NOMINATIM_BASE_URL", Settings.nominatim_base_url),
            user_agent=os.getenv("LEADGEN_USER_AGENT", Settings.user_agent),
            db_path=os.getenv("LEADGEN_DB_PATH", Settings.db_path),
            output_dir=os.getenv("LEADGEN_OUTPUT_DIR", Settings.output_dir),
            timeout_seconds=int(os.getenv("LEADGEN_TIMEOUT_SECONDS",
                                          str(Settings.timeout_seconds))),
            request_delay_seconds=float(os.getenv(
                "LEADGEN_REQUEST_DELAY_SECONDS",
                str(Settings.request_delay_seconds))),
        )
        s.extra_env = extra
        _settings = s
    return _settings
