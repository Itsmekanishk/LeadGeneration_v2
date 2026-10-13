"""Contact normalization tests (emails, phones)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.contact_normalizer import (  # noqa: E402
    extract_emails_from_text,
    extract_mailto_links,
    extract_phones_from_text,
    normalize_phone,
)
from app.utils.normalization import clean_category  # noqa: E402


def test_extracts_valid_emails():
    findings = extract_emails_from_text(
        "Contact info@sunrisehospital.in or care@sunrisehospital.in",
        source_url="https://sunrisehospital.in/contact",
        business_domain="sunrisehospital.in",
    )
    emails = {f.email for f in findings}
    assert emails == {"info@sunrisehospital.in", "care@sunrisehospital.in"}


def test_high_confidence_for_business_domain():
    findings = extract_emails_from_text(
        "write to info@sunrisehospital.in",
        source_url="https://sunrisehospital.in",
        business_domain="sunrisehospital.in",
    )
    assert findings[0].confidence == "HIGH"


def test_invalid_email_rejected():
    findings = extract_emails_from_text(
        "not-an-email @broken no@domain", source_url=""
    )
    assert findings == []


def test_junk_emails_filtered():
    findings = extract_emails_from_text(
        "placeholder example.com test@example.org", source_url=""
    )
    assert findings == []


def test_obfuscated_email_repaired():
    findings = extract_emails_from_text(
        "info [at] sunrisehospital [dot] in", source_url="https://sunrisehospital.in",
        business_domain="sunrisehospital.in",
    )
    assert any(f.email == "info@sunrisehospital.in" for f in findings)


def test_mailto_extraction():
    findings = extract_mailto_links(
        '<a href="mailto:INFO@SunriseHospital.in?subject=hi">mail</a>',
        page_url="https://sunrisehospital.in",
        business_domain="sunrisehospital.in",
    )
    assert findings and findings[0].email == "info@sunrisehospital.in"


def test_indian_phone_normalization():
    assert normalize_phone("+91 98765 43210") == ("+919876543210", "+91")
    assert normalize_phone("09876543210") == ("+919876543210", "+91")
    assert normalize_phone("9876543210") == ("+919876543210", "+91")


def test_invalid_phone_returns_empty():
    assert normalize_phone("") == ("", "")
    assert normalize_phone("123") == ("", "")


def test_lowercase_region_code_still_normalizes():
    """Job country codes are stored lowercase; ``phonenumbers`` needs UPPERCASE.

    Regression: ``normalize_phone(number, "in")`` used to raise
    ``NumberParseException`` internally and silently return an empty
    normalization, so every OSINT-discovered phone was stored un-normalized.
    """
    assert normalize_phone("9876543210", "in") == ("+919876543210", "+91")
    assert normalize_phone("09876543210", "in") == ("+919876543210", "+91")
    assert normalize_phone("9876543210", "") == ("+919876543210", "+91")
    assert normalize_phone("9876543210", "not-a-region") == ("+919876543210", "+91")


def test_directory_page_emails_are_downgraded():
    """An email on a third-party directory is never the business's own contact."""
    own = extract_emails_from_text(
        "support@practo.com",
        source_url="https://practo.com/pune/clinic/x",
        business_domain="practo.com",
    )
    assert own and own[0].confidence == "LOW"

    # A genuine business domain is unaffected.
    biz = extract_emails_from_text(
        "info@sunrisehospital.in",
        source_url="https://sunrisehospital.in/contact",
        business_domain="sunrisehospital.in",
    )
    assert biz and biz[0].confidence == "HIGH"


def test_clean_category_humanizes_hierarchies():
    assert clean_category("Hospitals") == "Hospitals"
    assert clean_category("healthcare, healthcare.hospital") == "Hospital"
    assert clean_category("catering.restaurant, catering") == "Restaurant"
    assert clean_category("") == ""


def test_unusable_mailboxes_are_filtered():
    """Bounce/abuse endpoints and template addresses are not leads."""
    findings = extract_emails_from_text(
        "noreply@x.com postmaster@x.com abuse@x.com yourname@x.com info@x.com",
        business_domain="x.com",
    )
    assert {f.email for f in findings} == {"info@x.com"}


def test_role_mailboxes_rank_above_personal():
    from app.services.contact_normalizer import email_priority, is_role_mailbox

    assert is_role_mailbox("info@x.com")
    assert not is_role_mailbox("drsmith@x.com")
    assert email_priority("info@x.com") < email_priority("sales.pune@x.com")
    assert email_priority("sales.pune@x.com") < email_priority("drsmith@x.com")


def test_phone_findings_carry_confidence_and_provenance():
    from app.services.contact_normalizer import extract_phone_findings

    findings = extract_phone_findings(
        "Call +91 98765 43210 or WhatsApp https://wa.me/919876500000 "
        "or landline 020 2665 4321",
        page_url="https://x.example/contact",
        default_country="in",
    )
    by_norm = {f.normalized: f for f in findings}
    assert by_norm["+919876500000"].confidence == "HIGH"     # WhatsApp link
    assert by_norm["+919876500000"].source == "whatsapp"
    assert by_norm["+919876543210"].confidence == "MEDIUM"   # body text
    assert all(f.source_url == "https://x.example/contact" for f in findings)
    assert findings[0].confidence == "HIGH"  # best-first ordering


def test_tel_scheme_with_whitespace_is_high_confidence():
    """"Tel: 020 …" is an explicit contact link (HIGH); "hotel: …" is not."""
    from app.services.contact_normalizer import extract_phone_findings

    explicit = extract_phone_findings("Tel: 020 2665 4321", default_country="in")
    assert [(f.source, f.confidence) for f in explicit] == [("tel", "HIGH")]

    inside_word = extract_phone_findings("hotel: 020 2665 4321", default_country="in")
    assert inside_word and all(f.source == "text" for f in inside_word)


def test_raw_phone_keeps_leading_parenthesis():
    """"(020) 2665 4321" must not lose its opening bracket in the raw value."""
    from app.services.contact_normalizer import extract_phone_findings

    found = extract_phone_findings("Phone: (020) 2665 4321", default_country="in")
    assert found and found[0].raw == "(020) 2665 4321"
    assert found[0].normalized == "+912026654321"


def test_phone_findings_normalize_with_job_region():
    """A lowercase job country must still normalize country-less numbers."""
    from app.services.contact_normalizer import extract_phone_findings

    findings = extract_phone_findings("Call 09876543210 now", default_country="in")
    assert any(f.normalized == "+919876543210" for f in findings)


def test_extract_phones_from_text():
    found = extract_phones_from_text("Call us at +91 98765 43210 today")
    assert found and "98765 43210" in found[0]
