"""Unit tests for src/services/bias_redaction.py."""

from src.services.bias_redaction import redact_protected_attributes


def test_clean_text_is_unchanged_and_reports_no_categories():
    text = "Experienced backend engineer skilled in Python, SQL, and distributed systems."
    redacted, categories = redact_protected_attributes(text)
    assert redacted == text
    assert categories == []


def test_age_proxy_graduation_year_is_redacted():
    text = "Graduated in 2003 from State University with a B.S. in Computer Science."
    redacted, categories = redact_protected_attributes(text)
    assert "age_proxy" in categories
    assert "2003" not in redacted
    assert "[REDACTED:AGE_PROXY]" in redacted


def test_gender_proxy_honorific_is_redacted():
    text = "Mrs. Tanaka led the backend team for three years."
    redacted, categories = redact_protected_attributes(text)
    assert "gender_proxy" in categories
    assert "Mrs." not in redacted


def test_disability_disclosure_is_redacted():
    text = "Requested a reasonable accommodation for a hearing impaired condition."
    redacted, categories = redact_protected_attributes(text)
    assert "disability_disclosure" in categories
    assert "hearing" not in redacted.lower() or "[REDACTED:DISABILITY_DISCLOSURE]" in redacted


def test_parental_leave_proxy_is_redacted():
    text = "Took maternity leave in 2022 before returning to lead the platform team."
    redacted, categories = redact_protected_attributes(text)
    assert "parental_leave_proxy" in categories
    assert "maternity leave" not in redacted.lower()


def test_religious_affiliation_proxy_is_redacted():
    text = "Active volunteer at the Islamic Center and youth Bible Study group."
    redacted, categories = redact_protected_attributes(text)
    assert "religious_affiliation_proxy" in categories


def test_national_origin_proxy_is_redacted():
    text = "International student who required visa sponsorship for full-time roles."
    redacted, categories = redact_protected_attributes(text)
    assert "national_origin_proxy" in categories


def test_multiple_categories_can_be_detected_in_one_text():
    text = "Mrs. Lee, graduated in 1998, later took maternity leave in 2010."
    redacted, categories = redact_protected_attributes(text)
    assert "gender_proxy" in categories
    assert "age_proxy" in categories
    assert "parental_leave_proxy" in categories
    assert "1998" not in redacted
    assert "1010" not in redacted


def test_empty_text_returns_empty_and_no_categories():
    redacted, categories = redact_protected_attributes("")
    assert redacted == ""
    assert categories == []
