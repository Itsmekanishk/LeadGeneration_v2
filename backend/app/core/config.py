"""Typed application settings loaded from environment variables.

All credentials enter the system here and nowhere else. Values are validated
at startup; production provider configuration fails fast when credentials are
missing and ``PROVIDER_STRICT_CONFIG`` is true.
"""
from __future__ import annotations

from typing import Optional

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Process-wide registry of secret VALUES that must be redacted from logs.
# populated from config at startup; see core/logging_config.py
_REGISTERED_SECRETS: set[str] = set()


def register_secret(value: Optional[str]) -> None:
    """Register a secret value so logging filters can scrub it."""
    if value and len(value) >= 6:
        _REGISTERED_SECRETS.add(value)


def get_registered_secrets() -> set[str]:
    """Return all secret values registered for redaction (for tests)."""
    return set(_REGISTERED_SECRETS)


class Settings(BaseSettings):
    """Central, typed configuration. Import via ``get_settings()``."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: str = "development"
    secret_key: str = Field(default="dev-secret-change-me", min_length=16)
    encryption_key: str = ""
    frontend_origin: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000,http://localhost:8080"

    # --- Datastores ---
    database_url: str = "sqlite:///./locallead_dev.db"
    redis_url: str = "redis://localhost:6379/0"

    # --- Provider ---
    business_provider: str = "nominatim"
    google_maps_api_key: str = ""
    serper_api_key: str = ""
    geoapify_api_key: str = ""
    provider_strict_config: bool = True

    # --- OpenStreetMap geocoding (Nominatim) -------------------------------
    # Free, open geocoding that replaces all maps.googleapis.com endpoints.
    # Usage policy: honest User-Agent, <= 1 req/s, no bulk.
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = (
        "LocalLeadIntelligence/1.0 (+https://github.com/locallead-intel; "
        "contact@localleadintel.org)"
    )
    # Resolve job locations to coordinates via Nominatim when the query has
    # no explicit lat/lng (provider searches then use the resolved center).
    enable_nominatim_geocode: bool = True

    # --- Disify disposable-email checks (free & keyless) ---------------------
    # Adds a disposable-mail verdict to website-extracted emails; disposable
    # addresses are downgraded to LOW confidence.
    enable_disify_checks: bool = True
    # --- APILayer unified suite (one account, one key) -----------------------
    # A single APILayer key powers multiple products. When set:
    #  * mailboxlayer (email validation: format/MX/SMTP + disposable flag)
    #    upgrades the keyless Disify check and runs for HIGH-confidence
    #    emails too (DeliverabilityInfo attached to contact records).
    #  * leadgen's Numverify client also accepts this key when the
    #    NUMVERIFY_API_KEY-specific variable is absent.
    # Free tier: 250 req/mo per product. Leave empty for keyless operation.
    apilayer_api_key: str = ""
    # APILayer Mailboxlayer endpoint (https://apilayer.net/api/check)
    apilayer_mailbox_base_url: str = "https://apilayer.net/api/check"
    # Verify mailboxlayer even for HIGH-confidence business-domain emails.
    mailboxlayer_check_high_confidence: bool = False
    # Comma-separated fallback chain used when a search's requested provider
    # fails at runtime (rate limit, outage). "auto" = default provider, then
    # any *configured* alternative. Empty = no fallback.
    provider_fallback_chain: str = "auto"
    google_places_text_search_cost_per_call: float = 0.032
    google_places_details_cost_per_call: float = 0.017

    # --- Search limits ---
    max_results: int = Field(default=60, ge=1, le=200)
    default_radius_m: int = Field(default=3000, ge=100, le=50000)
    default_country: str = "in"
    default_language: str = "en"

    # --- Crawler & OSINT ---
    enable_website_crawling: bool = True
    enable_osint_resolution: bool = True
    max_pages_per_domain: int = Field(default=5, ge=1, le=20)
    crawl_timeout: int = Field(default=12, ge=3, le=60)
    crawl_concurrency: int = Field(default=4, ge=1, le=16)
    crawl_user_agent: str = (
        "LocalLeadBot/1.0 (+https://example.com/locallead-bot; "
        "compliant contact-intelligence crawler)"
    )
    crawl_rate_limit_per_domain_per_sec: float = 0.5
    crawl_max_content_bytes: int = 2_000_000
    crawl_job_concurrency: int = Field(default=4, ge=1, le=16)
    respect_robots_txt: bool = True
    ssrf_protection: bool = True
    # Comma-separated extra hostnames the SSRF guard may fetch even though they
    # resolve to private/loopback addresses (test rigs, on-prem crawlers).
    ssrf_allowed_hosts: str = ""

    # --- Rate limiting ---
    rate_limit_search_per_min: int = Field(default=10, ge=1, le=1000)
    rate_limit_api_per_min: int = Field(default=120, ge=10, le=10000)

    # --- Celery ---
    celery_task_soft_time_limit: int = 900
    celery_task_time_limit: int = 960

    # --- Exports ---
    pdf_storage_path: str = "./data/reports"
    export_max_rows: int = 5000

    # --- Email ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "LocalLead <no-reply@example.com>"
    password_reset_token_ttl_minutes: int = 30

    # --- Auth ---
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # --- Supabase Auth (Gmail / Google OAuth) ---
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""

    @field_validator("business_provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in {"mock", "google", "serper", "nominatim", "geoapify"}:
            raise ValueError(
                "BUSINESS_PROVIDER must be 'mock', 'google', 'serper', 'nominatim', or "
                f"'geoapify', got '{v}'"
            )
        return v

    @field_validator("secret_key")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        if v and (v.startswith("change-me") or v == "dev-secret-change-me"):
            if (os.getenv("APP_ENV") or "development") == "production":
                raise ValueError("SECRET_KEY must be changed for production")
        return v

    @model_validator(mode="after")
    def _register_secrets(self) -> "Settings":
        register_secret(self.secret_key)
        register_secret(self.encryption_key)
        register_secret(self.google_maps_api_key)
        register_secret(self.serper_api_key)
        register_secret(self.geoapify_api_key)
        register_secret(self.smtp_password)
        register_secret(self.supabase_anon_key)
        register_secret(self.supabase_jwt_secret)
        return self

    @model_validator(mode="after")
    def _validate_provider_config(self) -> "Settings":
        """Fail fast when a production provider is selected but unconfigured."""
        if self.business_provider == "google" and not self.google_maps_api_key:
            msg = (
                "BUSINESS_PROVIDER=google but GOOGLE_MAPS_API_KEY is empty. "
                "Set the key in .env or switch BUSINESS_PROVIDER=nominatim (free, open-source)."
            )
            if self.provider_strict_config:
                raise ValueError(msg)
        if self.business_provider == "serper" and not self.serper_api_key:
            msg = (
                "BUSINESS_PROVIDER=serper but SERPER_API_KEY is empty. "
                "Set the key in .env or switch BUSINESS_PROVIDER=nominatim (free, open-source)."
            )
            if self.provider_strict_config:
                raise ValueError(msg)
        if self.business_provider == "geoapify" and not self.geoapify_api_key:
            msg = (
                "BUSINESS_PROVIDER=geoapify but GEOAPIFY_API_KEY is empty. "
                "Set the key in .env or switch BUSINESS_PROVIDER=nominatim (free, open-source)."
            )
            if self.provider_strict_config:
                raise ValueError(msg)
        return self

    @property
    def fallback_chain(self) -> list[str]:
        """Parsed PROVIDER_FALLBACK_CHAIN.

        "auto"  → default provider first, then every other provider that has
                  credentials configured (mock excluded — it is a test rig).
        "google,serper" -> literal order, skipping unconfigured providers.
        "" (empty) → fallback disabled.
        """
        raw = (self.provider_fallback_chain or "").strip()
        if not raw:
            return []
        if raw.lower() == "auto":
            chain: list[str] = [self.business_provider]
            for name, key in (
                ("geoapify", self.geoapify_api_key),
                ("google", self.google_maps_api_key),
                ("serper", self.serper_api_key),
            ):
                if key and name not in chain:
                    chain.append(name)
            # Free OSM fallback — but never after mock, which is a test rig
            # explicitly chosen for offline/demo runs.
            if self.business_provider != "mock" and "nominatim" not in chain:
                chain.append("nominatim")
            return chain
        # Literal chain: keep the given order but drop providers with no
        # credentials configured (mock is always usable).
        known = {"google", "serper", "mock", "nominatim", "geoapify"}
        return [
            p
            for p in (x.strip().lower() for x in raw.split(","))
            if p in known
            and (
                p == "mock"
                or p == "nominatim"  # keyless, always configured
                or (p == "geoapify" and bool(self.geoapify_api_key))
                or (p == "google" and bool(self.google_maps_api_key))
                or (p == "serper" and bool(self.serper_api_key))
            )
        ]

    # ----- derived -----
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def crawl_delay_seconds(self) -> float:
        """Minimum delay between requests to the same domain."""
        rate = self.crawl_rate_limit_per_domain_per_sec
        return 1.0 / rate if rate > 0 else 2.0

    @property
    def ssrf_allowed_host_set(self) -> frozenset[str]:
        """Extra hostnames exempt from private-IP blocking (lowercased)."""
        return frozenset(
            h.strip().lower().rstrip(".") for h in self.ssrf_allowed_hosts.split(",") if h.strip()
        )

    @property
    def pdf_dir(self) -> Path:
        p = Path(self.pdf_storage_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def fernet_key(self) -> bytes:
        """Fernet key for encrypting provider keys at rest (admin-configured providers)."""
        if self.encryption_key:
            return self.encryption_key.encode()
        # Derive a stable key from SECRET_KEY (dev convenience only).
        import base64
        import hashlib

        digest = hashlib.sha256(self.secret_key.encode()).digest()
        return base64.urlsafe_b64encode(digest)


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (dependency-injectable)."""
    return Settings()


def new_secret(nbytes: int = 32) -> str:
    """Generate a random secret (used for password reset tokens, etc.)."""
    return secrets.token_urlsafe(nbytes)
