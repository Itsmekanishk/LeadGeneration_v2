# leadgen — Open-source OSINT lead generation engine.
# Copyright (c) 2026 LocalLead Intelligence contributors
# Licensed under the MIT License — see the LICENSE file at the repository root.
"""Numverify client: normalize raw phone strings to E.164.

Per the master prompt: the raw phone string goes to Numverify and only
``international_format`` is stored. When ``NUMVERIFY_API_KEY`` is missing
(or Numverify errors / rate-limits) normalization falls back to Google's
``phonenumbers`` library and then to conservative regex rules, so the
deduplication hash keeps working offline.

Every Numverify attempt is audit-logged (service="numverify").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from leadgen import audit
from leadgen.config import get_settings

_DIGITS_RE = re.compile(r"\D")


@dataclass
class PhoneResult:
    """Outcome of normalizing one raw phone string."""

    raw: str
    international_format: str = ""   # the ONLY normalized value stored
    country_code: str = ""
    country_prefix: str = ""
    line_type: str = ""
    valid: bool = False
    source: str = ""                 # numverify | phonenumbers | regex | ""
    audit: dict = field(default_factory=dict)


def _fallback_normalize(raw: str, default_country: str) -> PhoneResult:
    """phonenumbers library, then conservative regex — mirrors the backend."""
    result = PhoneResult(raw=raw)
    try:
        import phonenumbers  # type: ignore

        parsed = phonenumbers.parse(raw, default_country or "IN")
        if phonenumbers.is_valid_number(parsed):
            result.international_format = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            result.country_code = f"+{parsed.country_code}"
            result.valid = True
            result.source = "phonenumbers"
        return result
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — unparseable strings fall through
        return result

    digits = _DIGITS_RE.sub("", raw)
    if not digits:
        return result
    if digits.startswith("00"):
        digits = digits[2:]
    if raw.strip().startswith("+") or digits.startswith("91"):
        if len(digits) == 12 and digits.startswith("91"):
            result.international_format, result.country_code = f"+{digits}", "+91"
            result.valid, result.source = True, "regex"
            return result
    if len(digits) == 10 and digits[0] in "6789":
        result.international_format, result.country_code = f"+91{digits}", "+91"
        result.valid, result.source = True, "regex"
    elif len(digits) == 11 and digits.startswith("0"):
        result.international_format = f"+91{digits[1:]}"
        result.country_code = "+91"
        result.valid, result.source = True, "regex"
    return result


def normalize_phone(raw: str, default_country: str = "IN",
                    use_numverify: bool = True) -> PhoneResult:
    """Send *raw* to Numverify and return its ``international_format``.

    Falls back to the ``phonenumbers`` library / regex when no API key is
    configured. Never raises; failures return an invalid PhoneResult with an
    audit row explaining why.
    """
    s = get_settings()
    if not raw or not raw.strip():
        return PhoneResult(raw=raw or "")

    if use_numverify and s.numverify_api_key:
        result = audit.http_get(
            s.numverify_base_url,
            params={"access_key": s.numverify_api_key,
                    "number": raw.strip(),
                    "country_code": default_country or ""},
            api_exempt=True,
        )
        payload = result.json if isinstance(result.json, dict) else {}
        # Numverify's /api/validate payload is flat (valid, number,
        # international_format, ...); tolerate a nested {"info": {...}} shape
        # in case of proxy/vendor variants.
        nested = payload.get("info")
        info = nested if isinstance(nested, dict) else payload
        pr = PhoneResult(
            raw=raw,
            international_format=str(info.get("international_format") or ""),
            country_code=str(info.get("country_code") or ""),
            country_prefix=str(info.get("country_prefix") or ""),
            line_type=str(info.get("line_type") or ""),
            valid=bool(info.get("valid")),
            source="numverify" if result.ok and info else "",
            audit=audit.audit_record(result, "numverify"),
        )
        # Numverify error payloads look like {"error": {"code": ..., "info": ...}}
        if "error" in payload and not info:
            pr.audit["error"] = str(payload["error"])[:300]
        if pr.international_format:
            return pr
        # key present but the call failed → fall through to offline path
        fallback = _fallback_normalize(raw, default_country)
        fallback.audit = pr.audit
        if fallback.source:
            fallback.audit["fallback_used"] = fallback.source
        return fallback

    fallback = _fallback_normalize(raw, default_country)
    fallback.audit = {
        "service": "numverify", "url": "", "http_status": None,
        "response_time_ms": 0, "robots_allowed": True,
        "status_kind": "skipped_no_key", "error": "",
        "source": fallback.source,
    }
    return fallback
