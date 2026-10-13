"""Startup configuration validation.

Centralizes the fail-fast boot check for secrets and provider configuration:

- **Production** (``APP_ENV=production``): any missing credential required by
  the selected provider — or a default/placeholder ``SECRET_KEY`` — refuses to
  boot. Fail-fast prevents a silently misconfigured deployment from serving
  traffic with broken features.
- **Development**: the app boots so developers without keys can still work
  (mock mode needs none), but prints a prominent, actionable console banner
  telling the developer exactly which ``.env`` variable to set.

This module is the single place where the "how do I fix my config?" guidance
lives, so the hosting developer sees the same instructions whether they read
the README or the server console.
"""
from __future__ import annotations

import sys
from typing import Optional

from app.core.logging_config import event_logger

log = event_logger("locallead.startup")

# Values that signal "the developer copied .env.example but never replaced
# the placeholder". Treated exactly like an empty/missing value.
_PLACEHOLDERS = {
    "your_api_key_here",
    "your_key_here",
    "your-email-password-here",
    "your_email_password_here",
    "your-key",
    "changeme",
    "change-me",
}


def missing_secret(value: Optional[str]) -> bool:
    """True when *value* is empty, whitespace, or an obvious placeholder."""
    if value is None:
        return True
    stripped = value.strip()
    return not stripped or stripped.lower() in _PLACEHOLDERS


def _banner(lines: list[str]) -> str:
    """Format a visible console banner so the guidance cannot be missed."""
    width = max(len(line) for line in lines) + 6
    bar = "=" * width
    body = "\n".join(f"  {line:<{width - 4}}" for line in lines)
    return f"\n{bar}\n{body}\n{bar}"


def run_startup_checks(settings) -> None:  # noqa: ANN001 — Settings type would create a cycle
    """Validate configuration at boot; raise RuntimeError to refuse startup.

    Production: any misconfiguration is fatal (fail fast).
    Development: misconfiguration prints a clear banner instead, so the app
    still boots for frontend work against mock data.
    """
    problems: list[str] = []      # fatal in production
    warnings: list[str] = []     # console banner in development

    # --- SECRET_KEY -------------------------------------------------------
    if settings.secret_key.startswith("change-me") or settings.secret_key in (
        "dev-secret-change-me",
        "dev-secret-change-me-locally",
    ):
        message = (
            "SECRET_KEY is a default/placeholder value. "
            "Generate one with: openssl rand -hex 32"
        )
        if settings.is_production:
            problems.append(message)
        else:
            warnings.append(message)

    # --- Provider credentials ----------------------------------------------
    provider = settings.business_provider
    if provider == "google":
        if missing_secret(settings.google_maps_api_key):
            message = (
                "BUSINESS_PROVIDER=google but GOOGLE_MAPS_API_KEY is missing. "
                "Set it in .env (Places API (New) enabled) or switch to "
                "BUSINESS_PROVIDER=nominatim (free, open-source OpenStreetMap)."
            )
            (problems if settings.is_production else warnings).append(message)
    elif provider == "serper":
        if missing_secret(settings.serper_api_key):
            message = (
                "BUSINESS_PROVIDER=serper but SERPER_API_KEY is missing. "
                "Set it in .env (https://serper.dev) or switch to "
                "BUSINESS_PROVIDER=nominatim (free, open-source OpenStreetMap)."
            )
            (problems if settings.is_production else warnings).append(message)
    elif provider == "geoapify":
        if missing_secret(settings.geoapify_api_key):
            message = (
                "BUSINESS_PROVIDER=geoapify but GEOAPIFY_API_KEY is missing. "
                "Set it in .env (https://www.geoapify.com) or switch to "
                "BUSINESS_PROVIDER=nominatim (free, open-source OpenStreetMap)."
            )
            (problems if settings.is_production else warnings).append(message)

    # --- SMTP (optional feature, warn only) --------------------------------
    if missing_secret(settings.smtp_host):
        message = "SMTP_HOST is not set — password-reset emails will not be sent."
        if settings.is_production:
            warnings.append(message)
        else:
            warnings.append(message)

    # --- Emit ---------------------------------------------------------------
    if warnings:
        banner = _banner([
            "⚠  LocalLead configuration warnings",
            "",
            *warnings,
            "",
            "Fix: edit .env (copy .env.example if it does not exist) and restart.",
        ])
        print(banner, file=sys.stderr)
        log.warning("startup_config_warnings", count=len(warnings))

    if problems and settings.is_production:
        raise RuntimeError(
            "Refusing to start in production with missing configuration:\n  - "
            + "\n  - ".join(problems)
            + "\nSet the variables in the environment or in .env, then restart."
        )

    log.info(
        "startup_checks_passed",
        env=settings.app_env,
        provider=provider,
        warnings=len(warnings),
    )
